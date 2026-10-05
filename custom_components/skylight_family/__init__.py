"""The Skylight Family integration.

Coordinates family members (linked to existing HA `person.*` entities) with
the calendar/todo entities that belong to them, plus reusable task presets
that get re-applied to each member's to-do list every morning. This
integration owns no calendar/todo data itself — it points at entities HA
already has and lets Skylight HA (a separate wall-tablet app) read the
mapping via `sensor.skylight_family_<member>`.

Day-to-day management happens in the "Skylight" sidebar panel (see
`panel.py` / `websocket_api.py`); Settings -> Devices & Services still works
for the same things via the config subentry flows.
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

from . import panel, websocket_api
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
from .helpers import (
    async_apply_items,
    async_clear_completed,
    async_apply_preset_to_member,
    get_entry,
    members,
    presets,
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
    """Register the domain-wide pieces: the service and the panel's API."""

    async def _handle_apply_preset(call: ServiceCall) -> None:
        entry = get_entry(hass)
        if entry is None:
            raise HomeAssistantError("Skylight Family is not configured")

        preset_name = call.data[ATTR_PRESET].strip().lower()
        preset_subentry = next(
            (
                subentry
                for subentry in presets(entry)
                if subentry.title.strip().lower() == preset_name
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

            await async_apply_preset_to_member(
                hass, member_subentry, preset_subentry
            )

    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_PRESET, _handle_apply_preset, schema=APPLY_PRESET_SCHEMA
    )
    websocket_api.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _seed_builtin_presets(hass, entry)

    await panel.async_register(hass)
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
    # The panel is intentionally left registered — see panel.py.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Take the sidebar entry away when the integration is removed for good."""
    panel.async_remove(hass)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _seed_builtin_presets(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create the built-in presets as ordinary preset subentries, once.

    After this they're just regular subentries — editable/deletable the
    same as anything the user creates themselves.
    """
    if presets(entry):
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
        for subentry in presets(entry)
    }
    today_key = WEEKDAYS[dt_util.now().weekday()][0]
    today_field = WEEKDAY_PRESET_FIELDS[today_key]

    for subentry in members(entry):
        todo_entity_id = subentry.data.get(CONF_TODO)
        if not todo_entity_id:
            continue

        await async_clear_completed(hass, todo_entity_id)

        preset_id = subentry.data.get(today_field)
        items = presets_by_id.get(preset_id, []) if preset_id else []
        if items:
            await async_apply_items(hass, todo_entity_id, items)
