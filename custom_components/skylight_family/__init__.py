"""The Skylight Family integration.

Coordinates family members (linked to existing HA `person.*` entities) with
the calendar/todo entities that belong to them, plus reusable task presets
that get re-applied to each member's to-do list every morning. This
integration owns no calendar/todo data itself — it points at entities HA
already has and lets Skylight HA (a separate wall-tablet app) read the
mapping via `sensor.skylight_family_<member>`.
"""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_PRESET,
    BUILTIN_PRESETS,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_TODO,
    DEFAULT_RESET_TIME,
    DOMAIN,
    SERVICE_APPLY_PRESET,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
    WEEKDAY_PRESET_FIELDS,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

APPLY_PRESET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_PRESET): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the skylight_family.apply_preset service once, domain-wide."""

    async def _handle_apply_preset(call: ServiceCall) -> None:
        entry = _get_the_entry(hass)
        if entry is None:
            raise HomeAssistantError("Skylight Family is not configured")

        preset_name = call.data[ATTR_PRESET].strip().lower()
        preset_subentry = next(
            (
                s
                for s in entry.subentries.values()
                if s.subentry_type == SUBENTRY_TYPE_PRESET
                and s.title.strip().lower() == preset_name
            ),
            None,
        )
        if preset_subentry is None:
            raise HomeAssistantError(f"No preset named '{call.data[ATTR_PRESET]}'")

        ent_reg = er.async_get(hass)
        for entity_id in call.data[ATTR_ENTITY_ID]:
            registry_entry = ent_reg.async_get(entity_id)
            member_subentry = (
                entry.subentries.get(registry_entry.config_subentry_id)
                if registry_entry and registry_entry.config_subentry_id
                else None
            )
            if (
                member_subentry is None
                or member_subentry.subentry_type != SUBENTRY_TYPE_MEMBER
            ):
                raise HomeAssistantError(
                    f"{entity_id} is not a Skylight Family member"
                )

            todo_entity_id = member_subentry.data.get(CONF_TODO)
            if not todo_entity_id:
                raise HomeAssistantError(
                    f"{member_subentry.title} has no to-do list configured"
                )

            await _reset_todo_list(
                hass, todo_entity_id, preset_subentry.data.get(CONF_PRESET_ITEMS, [])
            )

    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_PRESET, _handle_apply_preset, schema=APPLY_PRESET_SCHEMA
    )
    return True


def _get_the_entry(hass: HomeAssistant) -> ConfigEntry | None:
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _seed_builtin_presets(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    reset_time = dt_util.parse_time(
        entry.options.get(CONF_RESET_TIME, DEFAULT_RESET_TIME)
    )

    @callback
    def _handle_reset_time(_now: object) -> None:
        hass.async_create_task(_apply_daily_reset(hass, entry))

    unsub = async_track_time_change(
        hass,
        _handle_reset_time,
        hour=reset_time.hour,
        minute=reset_time.minute,
        second=reset_time.second,
    )
    entry.async_on_unload(unsub)

    # Reload on any change to options (new reset time) or subentries (a
    # member/preset added, edited, or removed) so it takes effect
    # immediately instead of needing a manual HA restart.
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _seed_builtin_presets(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the built-in presets as ordinary preset subentries, once.

    After this they're just regular subentries — editable/deletable the
    same as anything the user creates themselves.
    """
    has_presets = any(
        subentry.subentry_type == SUBENTRY_TYPE_PRESET
        for subentry in entry.subentries.values()
    )
    if has_presets:
        return

    for name, items in BUILTIN_PRESETS.items():
        hass.config_entries.async_add_subentry(
            entry,
            ConfigSubentry(
                data={CONF_PRESET_ITEMS: items},
                subentry_type=SUBENTRY_TYPE_PRESET,
                title=name,
                unique_id=None,
            ),
        )


async def _apply_daily_reset(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """For each member: clear yesterday's completed items, then apply
    whichever preset (if any) is assigned to today's weekday."""
    presets_by_id = {
        subentry.subentry_id: subentry.data.get(CONF_PRESET_ITEMS, [])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_PRESET
    }
    today_key = WEEKDAYS[dt_util.now().weekday()][0]
    today_field = WEEKDAY_PRESET_FIELDS[today_key]

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MEMBER:
            continue

        todo_entity_id = subentry.data.get(CONF_TODO)
        if not todo_entity_id:
            continue

        await _clear_completed_items(hass, todo_entity_id)

        preset_id = subentry.data.get(today_field)
        items = presets_by_id.get(preset_id, []) if preset_id else []
        if items:
            await _reset_todo_list(hass, todo_entity_id, items)


async def _clear_completed_items(hass: HomeAssistant, todo_entity_id: str) -> None:
    try:
        await hass.services.async_call(
            "todo",
            "remove_completed_items",
            {},
            target={"entity_id": todo_entity_id},
            blocking=True,
        )
    except Exception:  # noqa: BLE001 - entity may not support deletion
        _LOGGER.warning(
            "Could not clear completed items from %s", todo_entity_id
        )


async def _reset_todo_list(
    hass: HomeAssistant, todo_entity_id: str, preset_items: list[str]
) -> None:
    try:
        response = await hass.services.async_call(
            "todo",
            "get_items",
            {},
            target={"entity_id": todo_entity_id},
            blocking=True,
            return_response=True,
        )
    except Exception:  # noqa: BLE001 - entity may be temporarily unavailable
        _LOGGER.warning(
            "Could not read existing items from %s, skipping preset reset",
            todo_entity_id,
        )
        return

    existing = {
        item["summary"]
        for item in (response or {}).get(todo_entity_id, {}).get("items", [])
    }

    for item in preset_items:
        if item in existing:
            continue
        await hass.services.async_call(
            "todo",
            "add_item",
            {"item": item},
            target={"entity_id": todo_entity_id},
            blocking=True,
        )
