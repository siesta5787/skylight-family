"""Pocket-money ledger, with weekly compounding interest on long-term savings.

Two accounts per kid, short term and long term. You deposit and spend; the
long-term account earns an annual rate (set per member) credited every
**Monday**, matching the Monday-first week the star tracker already uses.

**Interest is never stored.** The store holds only the entries a human
entered; every balance and every interest line is recomputed by replaying
that ledger from the first entry forward. This is the whole reason the
design looks like this: back-dating an expense you forgot has to reduce all
the interest earned *after* that date, and replaying gets that for free.
Storing interest would mean reversing and reissuing it, which is exactly the
sort of thing that quietly drifts out of true.

All arithmetic is in integer cents via `Decimal` with explicit half-up
rounding. Floats would accumulate error over years of weekly compounding,
and money that doesn't add up is worse than money that's slightly wrong.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util import ulid as ulid_util

from .const import (
    ACCOUNT_LONG,
    ACCOUNT_SHORT,
    ACCOUNTS,
    CONF_INTEREST_RATE,
    CONF_MONEY_ENABLED,
    DEFAULT_INTEREST_RATE,
    DOMAIN,
    KIND_DEPOSIT,
    KIND_EXPENSE,
    KIND_INTEREST,
    MONEY_STORAGE_KEY,
    MONEY_STORAGE_VERSION,
    WEEKS_PER_YEAR,
)
from .helpers import members

_LOGGER = logging.getLogger(__name__)

# Nothing here depends on an external system, so this interval only exists to
# keep the "accruing" figure moving as the days pass.
UPDATE_INTERVAL = timedelta(hours=1)


def money_enabled(subentry: ConfigSubentry) -> bool:
    return bool(subentry.data.get(CONF_MONEY_ENABLED, False))


def interest_rate(subentry: ConfigSubentry) -> Decimal:
    """The member's annual rate as a fraction (3.0% -> Decimal("0.03"))."""
    raw = subentry.data.get(CONF_INTEREST_RATE, DEFAULT_INTEREST_RATE)
    return Decimal(str(raw)) / Decimal(100)


def tracked_members(entry: ConfigEntry) -> list[ConfigSubentry]:
    return [subentry for subentry in members(entry) if money_enabled(subentry)]


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def signed_cents(entry: dict[str, Any]) -> int:
    """Entries store a positive amount plus a kind; the sign comes from here."""
    amount = int(entry["amount_cents"])
    return -amount if entry["kind"] == KIND_EXPENSE else amount


def _sort_key(entry: dict[str, Any]) -> tuple[str, str]:
    # Date first, then id — ULIDs are time-ordered, so two entries on the
    # same date replay in the order they were actually added.
    return (entry["date"], entry["id"])


class MoneyStore:
    """Wraps a Store holding `{member_id: [entry, ...]}`.

    Deliberately never pruned: unlike star history, a ledger that silently
    forgets old entries would make today's balance wrong.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, list[dict[str, Any]]]] = Store(
            hass, MONEY_STORAGE_VERSION, MONEY_STORAGE_KEY
        )
        self._data: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        self._data = await self._store.async_load() or {}

    @callback
    def async_schedule_save(self) -> None:
        self._store.async_delay_save(lambda: self._data, 5)

    @callback
    def entries(self, member_id: str) -> list[dict[str, Any]]:
        return sorted(self._data.get(member_id, []), key=_sort_key)

    @callback
    def add(
        self,
        member_id: str,
        account: str,
        kind: str,
        amount_cents: int,
        day: date,
        note: str,
    ) -> dict[str, Any]:
        if account not in ACCOUNTS:
            raise HomeAssistantError(f"Unknown account '{account}'")
        if kind not in (KIND_DEPOSIT, KIND_EXPENSE):
            raise HomeAssistantError(f"Unknown entry type '{kind}'")
        if amount_cents <= 0:
            raise HomeAssistantError("Amount must be more than zero")
        if day > dt_util.now().date():
            raise HomeAssistantError(f"{day.isoformat()} hasn't happened yet")

        entry = {
            "id": ulid_util.ulid_now(),
            "date": day.isoformat(),
            "account": account,
            "kind": kind,
            "amount_cents": int(amount_cents),
            "note": note.strip(),
        }
        self._data.setdefault(member_id, []).append(entry)
        self.async_schedule_save()
        return entry

    @callback
    def delete(self, member_id: str, entry_id: str) -> dict[str, Any]:
        entries = self._data.get(member_id, [])
        for index, entry in enumerate(entries):
            if entry["id"] == entry_id:
                removed = entries.pop(index)
                self.async_schedule_save()
                return removed
        raise HomeAssistantError("No such ledger entry")

    @callback
    def forget_member(self, member_id: str) -> None:
        if self._data.pop(member_id, None) is not None:
            self.async_schedule_save()


@callback
def replay(
    entries: list[dict[str, Any]],
    account: str,
    rate: Decimal,
    as_of: date,
) -> dict[str, Any]:
    """Replay one account's ledger, deriving interest as it goes.

    Returns the closing balance, the interest earned to date, the interest
    accrued so far in the current (not yet credited) week, and the full row
    list with a running balance on each — including the derived interest
    rows, which carry no `id` so the UI knows they can't be deleted.

    Interest for a week is credited on the *following* Monday, calculated on
    the balance as it stood at the end of that week.
    """
    relevant = [
        entry
        for entry in entries
        if entry["account"] == account and entry["date"] <= as_of.isoformat()
    ]
    earns_interest = account == ACCOUNT_LONG and rate > 0

    empty = {
        "balance_cents": 0,
        "interest_total_cents": 0,
        "accruing_cents": 0,
        "last_interest_date": None,
        "rows": [],
    }
    if not relevant:
        return empty

    weekly_rate = rate / Decimal(WEEKS_PER_YEAR)
    balance = 0
    interest_total = 0
    last_interest_date: str | None = None
    rows: list[dict[str, Any]] = []

    by_week: dict[date, list[dict[str, Any]]] = {}
    for entry in relevant:
        by_week.setdefault(monday_of(date.fromisoformat(entry["date"])), []).append(entry)

    week = monday_of(date.fromisoformat(relevant[0]["date"]))
    current_week = monday_of(as_of)

    while week <= current_week:
        for entry in by_week.get(week, []):
            balance += signed_cents(entry)
            rows.append({**entry, "balance_cents": balance})

        # Close the week off: anything before the current one has had its
        # interest credited on the Monday that follows it.
        if earns_interest and week < current_week and balance > 0:
            credited = _round_cents(Decimal(balance) * weekly_rate)
            if credited > 0:
                balance += credited
                interest_total += credited
                paid_on = week + timedelta(days=7)
                last_interest_date = paid_on.isoformat()
                rows.append(
                    {
                        "id": None,
                        "date": paid_on.isoformat(),
                        "account": account,
                        "kind": KIND_INTEREST,
                        "amount_cents": credited,
                        "note": "",
                        "balance_cents": balance,
                    }
                )
        week += timedelta(days=7)

    # Part-week interest, shown but not credited, so the figure still moves
    # between Mondays. Jumps to the full week's worth when Monday lands.
    accruing = 0
    if earns_interest and balance > 0:
        elapsed_days = (as_of - current_week).days
        accruing = _round_cents(
            Decimal(balance) * weekly_rate * Decimal(elapsed_days) / Decimal(7)
        )

    return {
        "balance_cents": balance,
        "interest_total_cents": interest_total,
        "accruing_cents": accruing,
        "last_interest_date": last_interest_date,
        "rows": rows,
    }


@callback
def member_summary(
    entries: list[dict[str, Any]], rate: Decimal, as_of: date
) -> dict[str, Any]:
    """Both accounts' closing figures, without the row lists."""
    summary: dict[str, Any] = {}
    for account in ACCOUNTS:
        result = replay(entries, account, rate, as_of)
        result.pop("rows")
        summary[account] = result
    return summary


class MoneyCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Recomputes every tracked member's balances.

    The work is pure local arithmetic, so the hourly interval is just there
    to keep the part-week "accruing" figure current as days tick over.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_money",
            update_interval=UPDATE_INTERVAL,
        )
        self.store = MoneyStore(hass)

    async def async_prepare(self) -> None:
        await self.store.async_load()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        as_of = dt_util.now().date()
        return {
            subentry.subentry_id: member_summary(
                self.store.entries(subentry.subentry_id),
                interest_rate(subentry),
                as_of,
            )
            for subentry in tracked_members(self.config_entry)
        }

    @callback
    def ledger(self, subentry: ConfigSubentry, account: str | None) -> dict[str, Any]:
        """Rows for one member, newest first, with the running balance."""
        as_of = dt_util.now().date()
        rate = interest_rate(subentry)
        entries = self.store.entries(subentry.subentry_id)

        wanted = ACCOUNTS if account is None else (account,)
        rows: list[dict[str, Any]] = []
        totals: dict[str, Any] = {}
        for name in wanted:
            result = replay(entries, name, rate, as_of)
            rows.extend(result.pop("rows"))
            totals[name] = result

        # Newest first for reading; ties broken so a day's interest sorts
        # after the entries it was earned on.
        rows.sort(key=lambda row: (row["date"], row["kind"] == KIND_INTEREST), reverse=True)
        return {"rows": rows, "totals": totals}

    async def async_add(self, **kwargs: Any) -> dict[str, Any]:
        entry = self.store.add(**kwargs)
        await self.async_refresh()
        return entry

    async def async_delete(self, member_id: str, entry_id: str) -> dict[str, Any]:
        removed = self.store.delete(member_id, entry_id)
        await self.async_refresh()
        return removed
