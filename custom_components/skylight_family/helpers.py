"""Shared lookups and to-do list operations.

These live in their own module rather than in `__init__.py` so that both the
service handler and the panel's WebSocket API can use them without one
importing the package's `__init__` back into itself.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.auth.models import User
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .const import (
    CONF_PANEL_ADMIN_ONLY,
    CONF_PRESET_ITEMS,
    CONF_TODO,
    DEFAULT_PANEL_ADMIN_ONLY,
    DOMAIN,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
)

_LOGGER = logging.getLogger(__name__)


def get_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The single Skylight Family config entry, or None if not set up."""
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def require_entry(hass: HomeAssistant) -> ConfigEntry:
    """Like get_entry, but raise a user-facing error when not configured."""
    entry = get_entry(hass)
    if entry is None:
        raise HomeAssistantError("Skylight Family is not configured")
    return entry


def panel_is_admin_only(hass: HomeAssistant) -> bool:
    entry = get_entry(hass)
    if entry is None:
        return DEFAULT_PANEL_ADMIN_ONLY
    return entry.options.get(CONF_PANEL_ADMIN_ONLY, DEFAULT_PANEL_ADMIN_ONLY)


def check_panel_write_access(hass: HomeAssistant, user: User | None) -> None:
    """Gate the panel's write commands on the same switch as the sidebar.

    Deliberately *not* a flat `@require_admin`: the panel is an editor with
    no read-only mode, so if someone turns the admin-only setting off to let
    the rest of the household use it, they need to be able to actually use
    it rather than hit "Unauthorized" on every button.
    """
    if user is None or (panel_is_admin_only(hass) and not user.is_admin):
        raise Unauthorized


def subentries_of(entry: ConfigEntry, subentry_type: str) -> list[ConfigSubentry]:
    """All subentries of one type, sorted by title for stable display order."""
    return sorted(
        (s for s in entry.subentries.values() if s.subentry_type == subentry_type),
        key=lambda s: s.title.lower(),
    )


def members(entry: ConfigEntry) -> list[ConfigSubentry]:
    return subentries_of(entry, SUBENTRY_TYPE_MEMBER)


def presets(entry: ConfigEntry) -> list[ConfigSubentry]:
    return subentries_of(entry, SUBENTRY_TYPE_PRESET)


def require_subentry(
    entry: ConfigEntry, subentry_id: str, subentry_type: str
) -> ConfigSubentry:
    """Look up a subentry by id, insisting it's of the expected type."""
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != subentry_type:
        label = "family member" if subentry_type == SUBENTRY_TYPE_MEMBER else "preset"
        raise HomeAssistantError(f"No such {label} ({subentry_id})")
    return subentry


async def async_clear_completed(hass: HomeAssistant, todo_entity_id: str) -> None:
    """Drop items already checked off, so a list doesn't accumulate forever."""
    try:
        await hass.services.async_call(
            "todo",
            "remove_completed_items",
            {},
            target={"entity_id": todo_entity_id},
            blocking=True,
        )
    except Exception:  # noqa: BLE001 - entity may not support deletion
        _LOGGER.warning("Could not clear completed items from %s", todo_entity_id)


async def async_apply_items(
    hass: HomeAssistant, todo_entity_id: str, items: list[str]
) -> int:
    """Add any of `items` that aren't already on the list. Returns how many.

    Matching is by exact summary text, so re-applying a preset is a no-op
    rather than a way to end up with five "Brush teeth" entries.
    """
    if not items:
        return 0

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
            "Could not read existing items from %s, skipping preset", todo_entity_id
        )
        return 0

    existing = {
        item["summary"]
        for item in (response or {}).get(todo_entity_id, {}).get("items", [])
    }

    added = 0
    for item in items:
        if item in existing:
            continue
        await hass.services.async_call(
            "todo",
            "add_item",
            {"item": item},
            target={"entity_id": todo_entity_id},
            blocking=True,
        )
        added += 1
    return added


async def async_apply_preset_to_member(
    hass: HomeAssistant, member: ConfigSubentry, preset: ConfigSubentry
) -> int:
    """Push a preset's items onto a member's to-do list right now."""
    todo_entity_id = member.data.get(CONF_TODO)
    if not todo_entity_id:
        raise HomeAssistantError(f"{member.title} has no to-do list configured")
    return await async_apply_items(
        hass, todo_entity_id, preset.data.get(CONF_PRESET_ITEMS, [])
    )
