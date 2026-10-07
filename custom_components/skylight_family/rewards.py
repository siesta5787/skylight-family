"""Star / reward tracking.

The household rule this implements: a kid earns one star a day for getting
through that day's chores; collecting `star_goal` stars (default 6) in a
Monday-to-Sunday week earns a weekly prize; and earning today's star earns
tablet time *tomorrow*.

Two things are worth understanding before changing anything here.

**Today's star is live, past days are frozen.** Today's value is recomputed
from the member's real to-do list every time that list changes, so the panel
and the wall tablet both show progress as it happens. It is only written to
the store at the daily reset — which it has to be, because the same reset
clears completed items off the list and destroys the evidence. Hence
`async_freeze_day` running *before* `async_clear_completed` in the daily job.

**A manual star always wins.** Manually setting or clearing a day writes
`source: "manual"`, and from then on the automatic result is ignored for that
day until it's cleared back to automatic. A day with no preset assigned can't
be earned automatically at all (there are no chores to measure), but it can
still be granted by hand.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_PRESET_ITEMS,
    CONF_REWARDS_ENABLED,
    CONF_STAR_GOAL,
    CONF_TODO,
    DEFAULT_STAR_GOAL,
    DOMAIN,
    REWARDS_KEEP_DAYS,
    SOURCE_AUTO,
    SOURCE_MANUAL,
    STORAGE_KEY,
    STORAGE_VERSION,
    WEEKDAY_PRESET_FIELDS,
    WEEKDAYS,
)
from .helpers import (
    day_of_week_position,
    members,
    ordered_weekdays,
    start_of_week,
    week_dates,
    week_start_index,
)

_LOGGER = logging.getLogger(__name__)

# Safety net only — the real trigger is the to-do list changing.
UPDATE_INTERVAL = timedelta(minutes=15)


def rewards_enabled(subentry: ConfigSubentry) -> bool:
    return bool(subentry.data.get(CONF_REWARDS_ENABLED, False))


def star_goal(subentry: ConfigSubentry) -> int:
    return int(subentry.data.get(CONF_STAR_GOAL, DEFAULT_STAR_GOAL))


def tracked_members(entry: ConfigEntry) -> list[ConfigSubentry]:
    return [subentry for subentry in members(entry) if rewards_enabled(subentry)]


class RewardsStore:
    """Thin wrapper over a Store holding `{member_id: {date: day_record}}`."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY
        )
        self._data: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self._data, 10)

    @callback
    def get_day(self, member_id: str, day: date) -> dict[str, Any] | None:
        return self._data.get(member_id, {}).get(day.isoformat())

    @callback
    def set_day(
        self, member_id: str, day: date, star: bool, source: str
    ) -> None:
        self._data.setdefault(member_id, {})[day.isoformat()] = {
            "star": star,
            "source": source,
        }
        self.async_schedule_save()

    @callback
    def clear_day(self, member_id: str, day: date) -> None:
        """Forget a day entirely, handing it back to automatic evaluation."""
        self._data.get(member_id, {}).pop(day.isoformat(), None)
        self.async_schedule_save()

    @callback
    def async_prune(self, today: date) -> None:
        cutoff = (today - timedelta(days=REWARDS_KEEP_DAYS)).isoformat()
        changed = False
        for member_id, days in list(self._data.items()):
            for day_key in [key for key in days if key < cutoff]:
                del days[day_key]
                changed = True
            if not days:
                del self._data[member_id]
                changed = True
        if changed:
            self.async_schedule_save()


class RewardsCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Keeps every tracked member's star state up to date.

    Refreshes when a watched to-do list changes rather than on a timer; the
    interval is only a backstop in case a change event is missed.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            # The default request-refresh debouncer waits 10s before the
            # first recompute, which would make ticking off the last chore
            # feel broken. Fire immediately, then coalesce the rest of the
            # burst.
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=2.0, immediate=True
            ),
        )
        self.store = RewardsStore(hass)

    async def async_prepare(self) -> None:
        """Load history and start watching the to-do lists we care about."""
        await self.store.async_load()
        self._async_watch_todo_lists()

    @callback
    def _async_watch_todo_lists(self) -> None:
        entity_ids = [
            subentry.data[CONF_TODO]
            for subentry in tracked_members(self.config_entry)
            if subentry.data.get(CONF_TODO)
        ]
        if not entity_ids:
            return

        @callback
        def _todo_changed(_event: Event) -> None:
            # Debounced by the coordinator, so a burst of item updates
            # collapses into one recompute.
            self.hass.async_create_task(self.async_request_refresh())

        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, entity_ids, _todo_changed)
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        today = dt_util.now().date()
        self.store.async_prune(today)
        return {
            subentry.subentry_id: await self._async_member_state(subentry, today)
            for subentry in tracked_members(self.config_entry)
        }

    async def _async_member_state(
        self, subentry: ConfigSubentry, today: date
    ) -> dict[str, Any]:
        member_id = subentry.subentry_id
        done, total = await self._async_chore_progress(subentry, today)
        # No chores assigned today means nothing to measure, so no automatic
        # star — a blank day has to be granted by hand.
        auto_star = total > 0 and done == total

        start_index = week_start_index(self.config_entry)
        days: dict[str, Any] = {}
        stars = 0
        for day in week_dates(today, start_index):
            if day > today:
                record = None
            elif day == today:
                record = self._resolve_today(member_id, today, auto_star)
            else:
                record = self.store.get_day(member_id, day)
            days[day.isoformat()] = record
            if record and record["star"]:
                stars += 1

        goal = star_goal(subentry)
        yesterday = self.store.get_day(member_id, today - timedelta(days=1))

        return {
            "name": subentry.title,
            "weekday_keys": [key for key, _label in ordered_weekdays(start_index)],
            "week_start": start_of_week(today, start_index).isoformat(),
            "today": today.isoformat(),
            "days": days,
            "stars": stars,
            "goal": goal,
            "prize_earned": stars >= goal,
            "stars_needed": max(goal - stars, 0),
            # Chances left this week, today included — so the prize is still
            # reachable exactly when stars_needed <= days_remaining.
            "days_remaining": 7 - day_of_week_position(today, start_index),
            "star_today": bool(days[today.isoformat()]["star"]),
            "star_today_source": days[today.isoformat()]["source"],
            "chores_done": done,
            "chores_total": total,
            # The whole point of the daily star: tablet time the day after.
            "tablet_time": bool(yesterday and yesterday["star"]),
        }

    @callback
    def _resolve_today(
        self, member_id: str, today: date, auto_star: bool
    ) -> dict[str, Any]:
        stored = self.store.get_day(member_id, today)
        if stored and stored["source"] == SOURCE_MANUAL:
            return stored
        return {"star": auto_star, "source": SOURCE_AUTO}

    async def _async_chore_progress(
        self, subentry: ConfigSubentry, today: date
    ) -> tuple[int, int]:
        """How many of today's preset items are completed, out of how many.

        Only items belonging to today's preset count. Extra things a parent
        adds to the list during the day are ignored rather than held against
        the kid.
        """
        todo_entity_id = subentry.data.get(CONF_TODO)
        if not todo_entity_id:
            return 0, 0

        today_key = WEEKDAYS[today.weekday()][0]
        preset_id = subentry.data.get(WEEKDAY_PRESET_FIELDS[today_key])
        preset = self.config_entry.subentries.get(preset_id) if preset_id else None
        expected = list(preset.data.get(CONF_PRESET_ITEMS, [])) if preset else []
        if not expected:
            return 0, 0

        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {},
                target={"entity_id": todo_entity_id},
                blocking=True,
                return_response=True,
            )
        except Exception:  # noqa: BLE001 - entity may be unavailable
            _LOGGER.debug("Could not read %s for star tracking", todo_entity_id)
            return 0, len(expected)

        statuses = {
            item["summary"]: item.get("status")
            for item in (response or {}).get(todo_entity_id, {}).get("items", [])
        }
        # An expected item that's been deleted off the list counts as done —
        # some to-do integrations remove rather than complete.
        done = sum(
            1
            for summary in expected
            if statuses.get(summary, "completed") == "completed"
        )
        return done, len(expected)

    async def async_freeze_day(self, day: date) -> None:
        """Write each tracked member's star for `day` into the store.

        Called from the daily job before completed items get cleared, since
        that's the last moment the evidence still exists. Manual entries are
        left alone.
        """
        for subentry in tracked_members(self.config_entry):
            member_id = subentry.subentry_id
            stored = self.store.get_day(member_id, day)
            if stored and stored["source"] == SOURCE_MANUAL:
                continue
            done, total = await self._async_chore_progress(subentry, day)
            self.store.set_day(member_id, day, total > 0 and done == total, SOURCE_AUTO)

    @callback
    def week_snapshot(self, member_id: str, start: date) -> dict[str, Any]:
        """One member's stars for an arbitrary week, for browsing history.

        Past days come from the store; today comes from the live state so
        the current week reads the same here as it does on the entities.
        """
        today = dt_util.now().date()
        live = (self.data or {}).get(member_id, {})
        # `start` is already a week-start date, so step through it directly
        # rather than re-deriving it.
        days: dict[str, Any] = {}
        stars = 0
        for day in (start + timedelta(days=offset) for offset in range(7)):
            if day > today:
                record = None
            elif day == today:
                record = live.get("days", {}).get(day.isoformat())
            else:
                record = self.store.get_day(member_id, day)
            days[day.isoformat()] = record
            if record and record["star"]:
                stars += 1
        return {"week_start": start.isoformat(), "days": days, "stars": stars}

    async def async_set_star(
        self, member_id: str, day: date, star: bool | None
    ) -> None:
        """Manually award/revoke a star, or hand the day back to automatic."""
        # Days that haven't happened are always reported as "nothing yet", so
        # accepting a star for one would silently throw it away.
        if day > dt_util.now().date():
            raise HomeAssistantError(f"{day.isoformat()} hasn't happened yet")
        if star is None:
            self.store.clear_day(member_id, day)
        else:
            self.store.set_day(member_id, day, star, SOURCE_MANUAL)
        await self.async_refresh()
