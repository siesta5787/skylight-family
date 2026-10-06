"""Sensor platform for Skylight Family.

One mapping sensor per family member — its state isn't meaningful, it exists
purely to carry the person/calendar/todo/preset mapping as attributes, which
is what Skylight HA (or anything else) reads to build its member list.

Members with reward tracking turned on also get a star sensor, whose state
*is* meaningful: how many stars they've collected this week.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import (
    CONF_CALENDARS,
    CONF_COLOR,
    CONF_PERSON,
    CONF_TODO,
    SUBENTRY_TYPE_MEMBER,
    WEEKDAY_PRESET_FIELDS,
    WEEKDAYS,
)
from .rewards import RewardsCoordinator, rewards_enabled


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RewardsCoordinator = entry.runtime_data

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MEMBER:
            continue
        entities: list[SensorEntity] = [SkylightFamilyMemberSensor(entry, subentry)]
        if rewards_enabled(subentry):
            entities.append(SkylightFamilyStarsSensor(coordinator, entry, subentry))
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class SkylightFamilyMemberSensor(SensorEntity):
    """Carries one family member's calendar/todo mapping as attributes."""

    _attr_icon = "mdi:account-star"

    def __init__(self, entry: ConfigEntry, subentry: ConfigSubentry) -> None:
        self._entry = entry
        self._subentry = subentry
        name = subentry.data.get(CONF_NAME, subentry.title)
        self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}"
        self._attr_name = name
        self.entity_id = f"sensor.skylight_family_{slugify(name)}"

    @property
    def native_value(self) -> str:
        return "ok"

    @property
    def extra_state_attributes(self) -> dict:
        data = self._subentry.data

        presets: dict[str, str | None] = {}
        for day_key, _day_label in WEEKDAYS:
            preset_id = data.get(WEEKDAY_PRESET_FIELDS[day_key])
            preset_subentry = (
                self._entry.subentries.get(preset_id) if preset_id else None
            )
            presets[day_key] = preset_subentry.title if preset_subentry else None

        return {
            "person_entity_id": data.get(CONF_PERSON),
            "calendar_entity_ids": data.get(CONF_CALENDARS, []),
            "todo_entity_id": data.get(CONF_TODO),
            "color": data.get(CONF_COLOR),
            "presets": presets,
            "rewards_enabled": rewards_enabled(self._subentry),
        }


class SkylightFamilyStarsSensor(
    CoordinatorEntity[RewardsCoordinator], SensorEntity
):
    """Stars collected this week, with the whole week in the attributes.

    This is what the wall tablet reads to draw a member's row of stars — the
    `days` attribute is a date-keyed map so it can render past days, today
    (live) and not-yet-happened days (`null`) differently.
    """

    _attr_icon = "mdi:star"
    _attr_native_unit_of_measurement = "stars"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: RewardsCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
    ) -> None:
        super().__init__(coordinator)
        self._member_id = subentry.subentry_id
        name = subentry.data.get(CONF_NAME, subentry.title)
        self._attr_unique_id = f"{entry.entry_id}_{subentry.subentry_id}_stars"
        self._attr_name = f"{name} stars"
        self.entity_id = f"sensor.skylight_family_{slugify(name)}_stars"

    @property
    def _state(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._member_id)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    @property
    def native_value(self) -> int | None:
        state = self._state
        return state["stars"] if state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self._state
        if state is None:
            return None
        return {
            key: state[key]
            for key in (
                "week_start",
                "today",
                "days",
                "goal",
                "stars_needed",
                "days_remaining",
                "prize_earned",
                "star_today",
                "star_today_source",
                "chores_done",
                "chores_total",
                "tablet_time",
            )
        }
