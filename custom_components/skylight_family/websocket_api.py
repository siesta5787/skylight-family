"""WebSocket API backing the Skylight sidebar panel.

The panel reads and writes exactly the same member/preset subentries that
Settings -> Devices & Services manages — these commands are thin wrappers
over `hass.config_entries` subentry operations, so anything done from the
panel shows up in Settings and vice versa.

Mutations require admin. The read doesn't, leaving room to show a household
member their own routine read-only later on.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_CALENDARS,
    CONF_COLOR,
    CONF_PERSON,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_TODO,
    DEFAULT_RESET_TIME,
    DOMAIN,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
    WEEKDAY_PRESET_FIELDS,
    WEEKDAYS,
)
from .helpers import (
    async_apply_preset_to_member,
    get_entry,
    members,
    presets,
    require_entry,
    require_subentry,
)

WS_GET_CONFIG = f"{DOMAIN}/config"
WS_PRESET_CREATE = f"{DOMAIN}/preset/create"
WS_PRESET_UPDATE = f"{DOMAIN}/preset/update"
WS_PRESET_DELETE = f"{DOMAIN}/preset/delete"
WS_MEMBER_UPDATE = f"{DOMAIN}/member/update"
WS_APPLY_PRESET = f"{DOMAIN}/apply_preset"

_WEEKDAY_KEYS = [key for key, _label in WEEKDAYS]

# A day maps to a preset subentry_id, or to None/"" meaning "nothing today".
_ROUTINE_SCHEMA = vol.Schema(
    {vol.Optional(key): vol.Any(str, None) for key in _WEEKDAY_KEYS}
)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register every panel command. Called once, domain-wide."""
    for handler in (
        ws_get_config,
        ws_preset_create,
        ws_preset_update,
        ws_preset_delete,
        ws_member_update,
        ws_apply_preset,
    ):
        websocket_api.async_register_command(hass, handler)


def _member_payload(subentry: ConfigSubentry) -> dict[str, Any]:
    data = subentry.data
    return {
        "subentry_id": subentry.subentry_id,
        "name": subentry.title,
        CONF_PERSON: data.get(CONF_PERSON),
        CONF_CALENDARS: list(data.get(CONF_CALENDARS, [])),
        CONF_TODO: data.get(CONF_TODO),
        CONF_COLOR: data.get(CONF_COLOR),
        # weekday key -> preset subentry_id (or None). The panel resolves ids
        # to titles itself, from the presets list in the same payload.
        "routine": {
            key: data.get(WEEKDAY_PRESET_FIELDS[key]) for key in _WEEKDAY_KEYS
        },
    }


def _preset_payload(subentry: ConfigSubentry) -> dict[str, Any]:
    return {
        "subentry_id": subentry.subentry_id,
        "name": subentry.title,
        "items": list(subentry.data.get(CONF_PRESET_ITEMS, [])),
    }


def _clean_items(items: list[str]) -> list[str]:
    """Trim the blank lines a free-text item box inevitably produces."""
    return [item.strip() for item in items if item and item.strip()]


@websocket_api.websocket_command({vol.Required("type"): WS_GET_CONFIG})
@callback
def ws_get_config(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Everything the panel needs to draw itself, in one round trip."""
    entry = get_entry(hass)
    weekdays = [{"key": key, "label": label} for key, label in WEEKDAYS]

    if entry is None:
        connection.send_result(
            msg["id"],
            {
                "configured": False,
                "weekdays": weekdays,
                "reset_time": DEFAULT_RESET_TIME,
                "members": [],
                "presets": [],
            },
        )
        return

    connection.send_result(
        msg["id"],
        {
            "configured": True,
            "weekdays": weekdays,
            "reset_time": entry.options.get(CONF_RESET_TIME, DEFAULT_RESET_TIME),
            "members": [_member_payload(subentry) for subentry in members(entry)],
            "presets": [_preset_payload(subentry) for subentry in presets(entry)],
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PRESET_CREATE,
        vol.Required("name"): vol.All(str, vol.Length(min=1)),
        vol.Optional("items", default=[]): [str],
    }
)
@callback
def ws_preset_create(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Add a preset — same result as the 'Add preset' subentry flow."""
    entry = require_entry(hass)
    subentry = ConfigSubentry(
        data={CONF_PRESET_ITEMS: _clean_items(msg["items"])},
        subentry_type=SUBENTRY_TYPE_PRESET,
        title=msg["name"].strip(),
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    connection.send_result(msg["id"], _preset_payload(subentry))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PRESET_UPDATE,
        vol.Required("subentry_id"): str,
        vol.Required("name"): vol.All(str, vol.Length(min=1)),
        vol.Optional("items", default=[]): [str],
    }
)
@callback
def ws_preset_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rename a preset and/or replace its item list wholesale."""
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_PRESET)
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        title=msg["name"].strip(),
        data={CONF_PRESET_ITEMS: _clean_items(msg["items"])},
    )
    connection.send_result(msg["id"], _preset_payload(subentry))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PRESET_DELETE,
        vol.Required("subentry_id"): str,
    }
)
@callback
def ws_preset_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Delete a preset, first unassigning it from every member's routine.

    Without the unassign step a member keeps a dangling preset id for that
    weekday, which reads as "a preset is set" in the UI while the daily job
    silently applies nothing.
    """
    entry = require_entry(hass)
    preset_id = msg["subentry_id"]
    require_subentry(entry, preset_id, SUBENTRY_TYPE_PRESET)

    for member in members(entry):
        stale = [
            field
            for field in WEEKDAY_PRESET_FIELDS.values()
            if member.data.get(field) == preset_id
        ]
        if not stale:
            continue
        data = dict(member.data)
        for field in stale:
            data.pop(field, None)
        hass.config_entries.async_update_subentry(entry, member, data=data)

    hass.config_entries.async_remove_subentry(entry, preset_id)
    connection.send_result(msg["id"], {"removed": preset_id})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MEMBER_UPDATE,
        vol.Required("subentry_id"): str,
        vol.Optional("name"): vol.All(str, vol.Length(min=1)),
        vol.Optional(CONF_PERSON): str,
        vol.Optional(CONF_CALENDARS): [str],
        vol.Optional(CONF_TODO): vol.Any(str, None),
        vol.Optional(CONF_COLOR): vol.Any([int], None),
        vol.Optional("routine"): _ROUTINE_SCHEMA,
    }
)
@callback
def ws_member_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Patch a member — only the keys present in the message are touched.

    A merge rather than a replace, so the panel can save just the weekly
    routine without round-tripping (and risking clobbering) the person,
    calendar and colour fields it isn't editing at the time.
    """
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)

    data = dict(subentry.data)
    title = subentry.title

    if "name" in msg:
        title = msg["name"].strip()
        # Member subentries store the name in data as well as the title,
        # because the config flow's name field reads its default from data.
        data[CONF_NAME] = title

    for key in (CONF_PERSON, CONF_CALENDARS, CONF_TODO, CONF_COLOR):
        if key not in msg:
            continue
        if msg[key] in (None, ""):
            data.pop(key, None)
        else:
            data[key] = msg[key]

    if "routine" in msg:
        for day_key, preset_id in msg["routine"].items():
            field = WEEKDAY_PRESET_FIELDS[day_key]
            if not preset_id:
                data.pop(field, None)
                continue
            require_subentry(entry, preset_id, SUBENTRY_TYPE_PRESET)
            data[field] = preset_id

    hass.config_entries.async_update_subentry(entry, subentry, title=title, data=data)
    connection.send_result(msg["id"], _member_payload(subentry))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_APPLY_PRESET,
        vol.Required("member_id"): str,
        vol.Required("preset_id"): str,
    }
)
@websocket_api.async_response
async def ws_apply_preset(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Push a preset onto a member's to-do list now, outside the daily job."""
    entry = require_entry(hass)
    member = require_subentry(entry, msg["member_id"], SUBENTRY_TYPE_MEMBER)
    preset = require_subentry(entry, msg["preset_id"], SUBENTRY_TYPE_PRESET)

    added = await async_apply_preset_to_member(hass, member, preset)

    connection.send_result(
        msg["id"],
        {"added": added, "member": member.title, "preset": preset.title},
    )
