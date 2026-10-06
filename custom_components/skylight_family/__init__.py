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
from dataclasses import dataclass
from datetime import timedelta

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
    ACCOUNTS,
    ATTR_ACCOUNT,
    ATTR_AMOUNT,
    ATTR_DATE,
    ATTR_KIND,
    ATTR_NOTE,
    ATTR_PRESET,
    ATTR_TO_ACCOUNT,
    ATTR_STAR,
    BUILTIN_PRESETS,
    CONF_PANEL_ADMIN_ONLY,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_TODO,
    DEFAULT_PANEL_ADMIN_ONLY,
    DEFAULT_RESET_TIME,
    DOMAIN,
    MONEY_ENTITY_SUFFIXES,
    REWARD_ENTITY_SUFFIXES,
    SERVICE_ADD_MONEY,
    SERVICE_APPLY_PRESET,
    SERVICE_SET_STAR,
    ENTRY_KINDS,
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
from .money import MoneyCoordinator, money_enabled
from .rewards import RewardsCoordinator, rewards_enabled

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]

APPLY_PRESET_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_PRESET): cv.string,
    }
)

SET_STAR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        # Omit `star` entirely to hand the day back to automatic evaluation.
        vol.Optional(ATTR_STAR): cv.boolean,
        vol.Optional(ATTR_DATE): cv.date,
    }
)

ADD_MONEY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required(ATTR_ACCOUNT): vol.In(ACCOUNTS),
        vol.Required(ATTR_KIND): vol.In(ENTRY_KINDS),
        # Required when kind is transfer; the ledger rejects it otherwise.
        vol.Optional(ATTR_TO_ACCOUNT): vol.In(ACCOUNTS),
        # In currency units, e.g. 2.50 — converted to cents on the way in.
        vol.Required(ATTR_AMOUNT): vol.All(vol.Coerce(float), vol.Range(min=0.01)),
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_NOTE, default=""): cv.string,
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

        for member_subentry in _resolve_members(hass, entry, call):
            await async_apply_preset_to_member(
                hass, member_subentry, preset_subentry
            )

    async def _handle_set_star(call: ServiceCall) -> None:
        entry = get_entry(hass)
        if entry is None:
            raise HomeAssistantError("Skylight Family is not configured")

        coordinator = entry.runtime_data.rewards
        day = call.data.get(ATTR_DATE) or dt_util.now().date()
        star = call.data.get(ATTR_STAR)

        for member_subentry in _resolve_members(hass, entry, call):
            if not rewards_enabled(member_subentry):
                raise HomeAssistantError(
                    f"Reward tracking is turned off for {member_subentry.title}"
                )
            await coordinator.async_set_star(
                member_subentry.subentry_id, day, star
            )

    async def _handle_add_money(call: ServiceCall) -> None:
        entry = get_entry(hass)
        if entry is None:
            raise HomeAssistantError("Skylight Family is not configured")

        coordinator = entry.runtime_data.money
        day = call.data.get(ATTR_DATE) or dt_util.now().date()
        # Service callers think in currency units; the ledger thinks in cents.
        amount_cents = int(round(float(call.data[ATTR_AMOUNT]) * 100))

        for member_subentry in _resolve_members(hass, entry, call):
            if not money_enabled(member_subentry):
                raise HomeAssistantError(
                    f"Money tracking is turned off for {member_subentry.title}"
                )
            await coordinator.async_add(
                member_id=member_subentry.subentry_id,
                account=call.data[ATTR_ACCOUNT],
                to_account=call.data.get(ATTR_TO_ACCOUNT),
                kind=call.data[ATTR_KIND],
                amount_cents=amount_cents,
                day=day,
                note=call.data.get(ATTR_NOTE, ""),
            )

    hass.services.async_register(
        DOMAIN, SERVICE_APPLY_PRESET, _handle_apply_preset, schema=APPLY_PRESET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_STAR, _handle_set_star, schema=SET_STAR_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_MONEY, _handle_add_money, schema=ADD_MONEY_SCHEMA
    )
    websocket_api.async_register(hass)
    return True


def _resolve_members(
    hass: HomeAssistant, entry: ConfigEntry, call: ServiceCall
) -> list[ConfigSubentry]:
    """Map targeted entity_ids back to the member subentries that own them.

    Goes through the entity registry's `config_subentry_id` rather than
    parsing entity_ids, so it works for any of a member's entities (the
    mapping sensor, a star sensor, the tablet-time binary sensor) and
    doesn't break if a member is renamed.
    """
    ent_reg = er.async_get(hass)
    resolved: list[ConfigSubentry] = []
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
            raise HomeAssistantError(f"{entity_id} is not a Skylight Family member")
        if member_subentry not in resolved:
            resolved.append(member_subentry)
    return resolved


@dataclass
class SkylightFamilyRuntime:
    """What `entry.runtime_data` holds: one coordinator per feature."""

    rewards: RewardsCoordinator
    money: MoneyCoordinator


type SkylightFamilyEntry = ConfigEntry[SkylightFamilyRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: SkylightFamilyEntry) -> bool:
    _seed_builtin_presets(hass, entry)
    _remove_stale_feature_entities(hass, entry)

    rewards = RewardsCoordinator(hass, entry)
    await rewards.async_prepare()
    money = MoneyCoordinator(hass, entry)
    await money.async_prepare()

    entry.runtime_data = SkylightFamilyRuntime(rewards=rewards, money=money)
    await rewards.async_config_entry_first_refresh()
    await money.async_config_entry_first_refresh()

    await panel.async_register(
        hass,
        admin_only=entry.options.get(
            CONF_PANEL_ADMIN_ONLY, DEFAULT_PANEL_ADMIN_ONLY
        ),
    )
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


def _remove_stale_feature_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete a member's star or money entities once that feature is off.

    Platforms simply stop creating them, which leaves the registry entries
    behind as permanently `unavailable` entities cluttering the member's
    device page.

    Matches the known reward suffixes rather than "anything that isn't the
    mapping sensor", so adding some other per-member entity later doesn't
    silently get swept up by this.
    """
    ent_reg = er.async_get(hass)
    for subentry in members(entry):
        stale_suffixes: list[str] = []
        if not rewards_enabled(subentry):
            stale_suffixes += REWARD_ENTITY_SUFFIXES
        if not money_enabled(subentry):
            stale_suffixes += MONEY_ENTITY_SUFFIXES
        if not stale_suffixes:
            continue
        stale_ids = {
            f"{entry.entry_id}_{subentry.subentry_id}_{suffix}"
            for suffix in stale_suffixes
        }
        for registry_entry in er.async_entries_for_config_entry(
            ent_reg, entry.entry_id
        ):
            if registry_entry.unique_id in stale_ids:
                _LOGGER.debug(
                    "Removing %s, that feature is off for %s",
                    registry_entry.entity_id,
                    subentry.title,
                )
                ent_reg.async_remove(registry_entry.entity_id)


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
    """Freeze yesterday's stars, then clear completed items and apply today's
    presets.

    Order matters: clearing completed items destroys the only evidence of
    whether yesterday's chores were done, so the star has to be recorded
    first. See `rewards.py`.
    """
    coordinator = entry.runtime_data.rewards
    yesterday = dt_util.now().date() - timedelta(days=1)
    _LOGGER.debug("Daily reset: freezing stars for %s", yesterday)
    await coordinator.async_freeze_day(yesterday)

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

        _LOGGER.debug("Daily reset: clearing completed items from %s", todo_entity_id)
        await async_clear_completed(hass, todo_entity_id)

        preset_id = subentry.data.get(today_field)
        items = presets_by_id.get(preset_id, []) if preset_id else []
        _LOGGER.debug(
            "Daily reset: applying %d item(s) to %s", len(items), todo_entity_id
        )
        if items:
            await async_apply_items(hass, todo_entity_id, items)

    # Today's star starts over from an empty list, and tablet time now
    # reflects the star we just froze.
    await coordinator.async_refresh()
