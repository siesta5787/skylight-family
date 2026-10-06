"""WebSocket API backing the Skylight sidebar panel.

The panel reads and writes exactly the same member/preset subentries that
Settings -> Devices & Services manages — these commands are thin wrappers
over `hass.config_entries` subentry operations, so anything done from the
panel shows up in Settings and vice versa.

Writes are gated on the integration's own "admin only" option (the same
switch that controls who sees the sidebar entry) rather than a flat
`@require_admin`, so turning that off actually hands the panel to the rest
of the household instead of showing them a page where every button fails.
The read is never gated.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ACCOUNTS,
    CONF_CALENDARS,
    CONF_COLOR,
    CONF_INTEREST_RATE,
    CONF_MONEY_ENABLED,
    CONF_PERSON,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_REWARDS_ENABLED,
    CONF_STAR_GOAL,
    CONF_TODO,
    DEFAULT_INTEREST_RATE,
    DEFAULT_RESET_TIME,
    DEFAULT_STAR_GOAL,
    DOMAIN,
    ENTRY_KINDS,
    KIND_DEPOSIT,
    KIND_EXPENSE,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
    WEEKDAY_PRESET_FIELDS,
    WEEKDAYS,
)
from .helpers import (
    async_apply_preset_to_member,
    check_panel_write_access,
    get_entry,
    members,
    presets,
    require_entry,
    require_subentry,
)
from .money import interest_rate, money_enabled
from .money import tracked_members as money_members
from .rewards import (
    rewards_enabled,
    star_goal,
    tracked_members,
    week_start,
)

WS_GET_CONFIG = f"{DOMAIN}/config"
WS_PRESET_CREATE = f"{DOMAIN}/preset/create"
WS_PRESET_UPDATE = f"{DOMAIN}/preset/update"
WS_PRESET_DELETE = f"{DOMAIN}/preset/delete"
WS_MEMBER_UPDATE = f"{DOMAIN}/member/update"
WS_APPLY_PRESET = f"{DOMAIN}/apply_preset"
WS_REWARDS = f"{DOMAIN}/rewards"
WS_SET_STAR = f"{DOMAIN}/rewards/set_star"
WS_MONEY = f"{DOMAIN}/money"
WS_MONEY_LEDGER = f"{DOMAIN}/money/ledger"
WS_MONEY_ADD = f"{DOMAIN}/money/add"
WS_MONEY_UPDATE = f"{DOMAIN}/money/update"
WS_MONEY_DELETE = f"{DOMAIN}/money/delete"

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
        ws_rewards,
        ws_set_star,
        ws_money,
        ws_money_ledger,
        ws_money_add,
        ws_money_update,
        ws_money_delete,
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
        CONF_REWARDS_ENABLED: bool(data.get(CONF_REWARDS_ENABLED, False)),
        CONF_STAR_GOAL: int(data.get(CONF_STAR_GOAL, DEFAULT_STAR_GOAL)),
        CONF_MONEY_ENABLED: bool(data.get(CONF_MONEY_ENABLED, False)),
        CONF_INTEREST_RATE: float(
            data.get(CONF_INTEREST_RATE, DEFAULT_INTEREST_RATE)
        ),
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
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = ConfigSubentry(
        data={CONF_PRESET_ITEMS: _clean_items(msg["items"])},
        subentry_type=SUBENTRY_TYPE_PRESET,
        title=msg["name"].strip(),
        unique_id=None,
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    connection.send_result(msg["id"], _preset_payload(subentry))


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
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_PRESET)
    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        title=msg["name"].strip(),
        data={CONF_PRESET_ITEMS: _clean_items(msg["items"])},
    )
    connection.send_result(msg["id"], _preset_payload(subentry))


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
    check_panel_write_access(hass, connection.user)
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MEMBER_UPDATE,
        vol.Required("subentry_id"): str,
        vol.Optional("name"): vol.All(str, vol.Length(min=1)),
        vol.Optional(CONF_PERSON): str,
        vol.Optional(CONF_CALENDARS): [str],
        vol.Optional(CONF_TODO): vol.Any(str, None),
        vol.Optional(CONF_COLOR): vol.Any([int], None),
        vol.Optional(CONF_REWARDS_ENABLED): bool,
        vol.Optional(CONF_STAR_GOAL): vol.All(int, vol.Range(min=1, max=7)),
        vol.Optional(CONF_MONEY_ENABLED): bool,
        vol.Optional(CONF_INTEREST_RATE): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=100)
        ),
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
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)

    data = dict(subentry.data)
    title = subentry.title

    if "name" in msg:
        title = msg["name"].strip()
        # Member subentries store the name in data as well as the title,
        # because the config flow's name field reads its default from data.
        data[CONF_NAME] = title

    for key in (
        CONF_PERSON,
        CONF_CALENDARS,
        CONF_TODO,
        CONF_COLOR,
        CONF_REWARDS_ENABLED,
        CONF_STAR_GOAL,
        CONF_MONEY_ENABLED,
        CONF_INTEREST_RATE,
    ):
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
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    member = require_subentry(entry, msg["member_id"], SUBENTRY_TYPE_MEMBER)
    preset = require_subentry(entry, msg["preset_id"], SUBENTRY_TYPE_PRESET)

    added = await async_apply_preset_to_member(hass, member, preset)

    connection.send_result(
        msg["id"],
        {"added": added, "member": member.title, "preset": preset.title},
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_REWARDS,
        # Monday of the week to show. Defaults to the current week.
        vol.Optional("week_start"): cv.date,
    }
)
@callback
def ws_rewards(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Star state for every reward-tracked member, for one week."""
    entry = require_entry(hass)
    coordinator = entry.runtime_data.rewards

    today = dt_util.now().date()
    requested = msg.get("week_start") or today
    start = week_start(requested)

    payload = []
    for subentry in tracked_members(entry):
        member_id = subentry.subentry_id
        live = (coordinator.data or {}).get(member_id, {})
        snapshot = coordinator.week_snapshot(member_id, start)
        payload.append(
            {
                "subentry_id": member_id,
                "name": subentry.title,
                CONF_COLOR: subentry.data.get(CONF_COLOR),
                CONF_TODO: subentry.data.get(CONF_TODO),
                "goal": star_goal(subentry),
                "prize_earned": snapshot["stars"] >= star_goal(subentry),
                # Only meaningful for the current week; the panel shows them
                # on today's cell.
                "chores_done": live.get("chores_done", 0),
                "chores_total": live.get("chores_total", 0),
                "tablet_time": live.get("tablet_time", False),
                **snapshot,
            }
        )

    connection.send_result(
        msg["id"],
        {
            "today": today.isoformat(),
            "this_week_start": week_start(today).isoformat(),
            "weekdays": [{"key": key, "label": label} for key, label in WEEKDAYS],
            "members": payload,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SET_STAR,
        vol.Required("subentry_id"): str,
        vol.Required("date"): cv.date,
        # null hands the day back to automatic evaluation.
        vol.Required("star"): vol.Any(bool, None),
    }
)
@websocket_api.async_response
async def ws_set_star(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Award, revoke, or un-override a single day's star."""
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)
    if not rewards_enabled(subentry):
        raise HomeAssistantError(
            f"Reward tracking is turned off for {subentry.title}"
        )

    coordinator = entry.runtime_data.rewards
    await coordinator.async_set_star(subentry.subentry_id, msg["date"], msg["star"])
    connection.send_result(
        msg["id"], coordinator.week_snapshot(subentry.subentry_id, week_start(msg["date"]))
    )


def _money_payload(subentry: ConfigSubentry, summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "subentry_id": subentry.subentry_id,
        "name": subentry.title,
        CONF_COLOR: subentry.data.get(CONF_COLOR),
        "interest_rate": float(interest_rate(subentry) * 100),
        # Everything monetary crosses the wire as integer cents; the panel
        # formats it. Floats here would be a slow-motion rounding bug.
        "accounts": summary,
    }


@websocket_api.websocket_command({vol.Required("type"): WS_MONEY})
@callback
def ws_money(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Balances for every money-tracked member."""
    entry = require_entry(hass)
    data = entry.runtime_data.money.data or {}
    connection.send_result(
        msg["id"],
        {
            "currency": hass.config.currency,
            "today": dt_util.now().date().isoformat(),
            "members": [
                _money_payload(subentry, data.get(subentry.subentry_id, {}))
                for subentry in money_members(entry)
            ],
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MONEY_LEDGER,
        vol.Required("subentry_id"): str,
        # Omit for both accounts in one list.
        vol.Optional("account"): vol.In(ACCOUNTS),
    }
)
@callback
def ws_money_ledger(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """One member's ledger, newest first, with derived interest rows."""
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)
    if not money_enabled(subentry):
        raise HomeAssistantError(f"Money tracking is turned off for {subentry.title}")

    ledger = entry.runtime_data.money.ledger(subentry, msg.get("account"))
    connection.send_result(
        msg["id"],
        {
            "currency": hass.config.currency,
            "name": subentry.title,
            "interest_rate": float(interest_rate(subentry) * 100),
            **ledger,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MONEY_ADD,
        vol.Required("subentry_id"): str,
        vol.Required("account"): vol.In(ACCOUNTS),
        vol.Required("kind"): vol.In(ENTRY_KINDS),
        # Only meaningful for a transfer; validated in the store.
        vol.Optional("to_account"): vol.In(ACCOUNTS),
        vol.Required("amount_cents"): vol.All(int, vol.Range(min=1)),
        vol.Required("date"): cv.date,
        vol.Optional("note", default=""): str,
    }
)
@websocket_api.async_response
async def ws_money_add(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Record a deposit or an expense, on any date up to today."""
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)
    if not money_enabled(subentry):
        raise HomeAssistantError(f"Money tracking is turned off for {subentry.title}")

    added = await entry.runtime_data.money.async_add(
        member_id=subentry.subentry_id,
        account=msg["account"],
        to_account=msg.get("to_account"),
        kind=msg["kind"],
        amount_cents=msg["amount_cents"],
        day=msg["date"],
        note=msg["note"],
    )
    connection.send_result(msg["id"], added)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MONEY_UPDATE,
        vol.Required("subentry_id"): str,
        vol.Required("entry_id"): str,
        vol.Required("account"): vol.In(ACCOUNTS),
        vol.Required("kind"): vol.In(ENTRY_KINDS),
        vol.Optional("to_account"): vol.In(ACCOUNTS),
        vol.Required("amount_cents"): vol.All(int, vol.Range(min=1)),
        vol.Required("date"): cv.date,
        vol.Optional("note", default=""): str,
    }
)
@websocket_api.async_response
async def ws_money_update(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Rewrite an existing entry, keeping its id. Every field is replaced,
    so turning a transfer back into a deposit can't leave a stale
    destination account behind."""
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)
    if not money_enabled(subentry):
        raise HomeAssistantError(f"Money tracking is turned off for {subentry.title}")

    updated = await entry.runtime_data.money.async_update(
        member_id=subentry.subentry_id,
        entry_id=msg["entry_id"],
        account=msg["account"],
        to_account=msg.get("to_account"),
        kind=msg["kind"],
        amount_cents=msg["amount_cents"],
        day=msg["date"],
        note=msg["note"],
    )
    connection.send_result(msg["id"], updated)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_MONEY_DELETE,
        vol.Required("subentry_id"): str,
        vol.Required("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_money_delete(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove a ledger entry. Interest rows have no id and can't be removed —
    they're derived, so the way to change them is to fix the entries that
    produced them."""
    check_panel_write_access(hass, connection.user)
    entry = require_entry(hass)
    subentry = require_subentry(entry, msg["subentry_id"], SUBENTRY_TYPE_MEMBER)
    removed = await entry.runtime_data.money.async_delete(
        subentry.subentry_id, msg["entry_id"]
    )
    connection.send_result(msg["id"], removed)
