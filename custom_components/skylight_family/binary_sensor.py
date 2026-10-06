"""Binary sensor platform for Skylight Family.

Three per reward-tracked member, each answering one question an automation
or the wall tablet might actually want to act on:

- `star_today` — has today's star been earned yet (live, updates as chores
  get ticked off)
- `tablet_time` — is tablet time allowed *today*, i.e. was yesterday's star
  earned. This is the hook for whatever enforces screen time on the kids'
  devices.
- `weekly_prize` — has the week's star goal been reached
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .const import SUBENTRY_TYPE_MEMBER
from .rewards import RewardsCoordinator, rewards_enabled


@dataclass(frozen=True, kw_only=True)
class SkylightBinarySensorDescription(BinarySensorEntityDescription):
    """Maps an entity onto one key of the coordinator's per-member state."""

    state_key: str
    suffix: str
    label: str


DESCRIPTIONS: tuple[SkylightBinarySensorDescription, ...] = (
    SkylightBinarySensorDescription(
        key="star_today",
        state_key="star_today",
        suffix="star_today",
        label="star today",
        icon="mdi:star-check",
    ),
    SkylightBinarySensorDescription(
        key="tablet_time",
        state_key="tablet_time",
        suffix="tablet_time",
        label="tablet time",
        icon="mdi:tablet",
    ),
    SkylightBinarySensorDescription(
        key="weekly_prize",
        state_key="prize_earned",
        suffix="weekly_prize",
        label="weekly prize",
        icon="mdi:trophy",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: RewardsCoordinator = entry.runtime_data

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE_MEMBER:
            continue
        if not rewards_enabled(subentry):
            continue
        async_add_entities(
            [
                SkylightFamilyRewardBinarySensor(
                    coordinator, entry, subentry, description
                )
                for description in DESCRIPTIONS
            ],
            config_subentry_id=subentry.subentry_id,
        )


class SkylightFamilyRewardBinarySensor(
    CoordinatorEntity[RewardsCoordinator], BinarySensorEntity
):
    entity_description: SkylightBinarySensorDescription

    def __init__(
        self,
        coordinator: RewardsCoordinator,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        description: SkylightBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._member_id = subentry.subentry_id
        name = subentry.data.get(CONF_NAME, subentry.title)
        self._attr_unique_id = (
            f"{entry.entry_id}_{subentry.subentry_id}_{description.key}"
        )
        self._attr_name = f"{name} {description.label}"
        self.entity_id = (
            f"binary_sensor.skylight_family_{slugify(name)}_{description.suffix}"
        )

    @property
    def _state(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._member_id)

    @property
    def available(self) -> bool:
        return super().available and self._state is not None

    @property
    def is_on(self) -> bool | None:
        state = self._state
        if state is None:
            return None
        return bool(state[self.entity_description.state_key])
