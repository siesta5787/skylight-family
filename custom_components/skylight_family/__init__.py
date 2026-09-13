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

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    BUILTIN_PRESETS,
    CONF_PRESET,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_TODO,
    DEFAULT_RESET_TIME,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _seed_builtin_presets(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    reset_time = dt_util.parse_time(
        entry.options.get(CONF_RESET_TIME, DEFAULT_RESET_TIME)
    )
    unsub = async_track_time_change(
        hass,
        lambda _now: hass.async_create_task(_apply_daily_reset(hass, entry)),
        hour=reset_time.hour,
        minute=reset_time.minute,
        second=reset_time.second,
    )
    entry.async_on_unload(unsub)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


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
    """Re-add any missing preset items to each member's to-do list."""
    presets_by_id = {
        subentry.subentry_id: subentry.data.get(CONF_PRESET_ITEMS, [])
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_PRESET
    }

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MEMBER:
            continue

        todo_entity_id = subentry.data.get(CONF_TODO)
        preset_id = subentry.data.get(CONF_PRESET)
        if not todo_entity_id or not preset_id:
            continue

        items = presets_by_id.get(preset_id, [])
        if not items:
            continue

        await _reset_todo_list(hass, todo_entity_id, items)


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
