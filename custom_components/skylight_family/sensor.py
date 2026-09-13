"""Sensor platform for Skylight Family.

One sensor per family member. The state itself isn't meaningful — this
entity exists purely to carry the person/calendar/todo/preset mapping as
attributes, which is what Skylight HA (or anything else) reads to build its
member list.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import (
    CONF_CALENDARS,
    CONF_COLOR,
    CONF_PERSON,
    CONF_PRESET,
    CONF_TODO,
    SUBENTRY_TYPE_MEMBER,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MEMBER:
            continue
        async_add_entities(
            [SkylightFamilyMemberSensor(entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


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
        preset_id = data.get(CONF_PRESET)
        preset_title = None
        if preset_id and (preset_subentry := self._entry.subentries.get(preset_id)):
            preset_title = preset_subentry.title

        return {
            "person_entity_id": data.get(CONF_PERSON),
            "calendar_entity_ids": data.get(CONF_CALENDARS, []),
            "todo_entity_id": data.get(CONF_TODO),
            "color": data.get(CONF_COLOR),
            "preset": preset_title,
        }
