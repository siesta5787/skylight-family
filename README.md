# Skylight Family

A Home Assistant custom integration that coordinates family members with
their calendars and to-do lists, entirely through HA's Settings UI (no YAML).
It's the "backend" half for [Skylight HA](https://github.com/siesta5787/skylight-ha),
a wall-mounted family dashboard — this integration owns the *who's linked to
what*, Skylight HA owns the *display*.

## What it does

- **Add family members** — link each to an existing HA `person.*` entity,
  and pick which `calendar.*` entities and which `todo.*` list belong to
  them, plus a color.
- **Task presets** — reusable named lists of default to-do items (e.g.
  "School-age kid": brush teeth, make bed, pack school bag). Three presets
  (School-age kid, Toddler, Adult) are seeded on first setup; edit or
  delete them like any other preset.
- **A different preset per day of the week, per member** — each member has
  7 independent preset slots (Monday–Sunday); leave a day blank to apply no
  preset that day. Every morning (default 04:00, configurable) the
  integration clears completed items off each member's to-do list and adds
  back whichever items are missing from that day's assigned preset.
- **Apply a preset on demand** — push a preset onto someone's to-do list
  right now, independent of the daily schedule: a button in the sidebar
  panel, or the `skylight_family.apply_preset` service from an automation.
- **A "Skylight" sidebar panel** — the day-to-day UI, so none of this needs
  digging through Settings. Two tabs:
  - **People** — each member's seven weekday preset dropdowns, a "push a
    preset right now" button, and a collapsible form for which person,
    to-do list, calendars and colour belong to them.
  - **Presets** — view, add, edit and delete presets, and apply one to a
    member from that side too.
  - **Rewards** — each tracked kid's week of stars, tappable to award or
    take one back, with a pager for earlier weeks.

  It's a view of the same data as Settings → Devices & Services, not a
  parallel store, so you can use either. Adding and removing *members*
  still happens in Settings. Admin-only by default, switchable from the
  integration's Configure page.
- **Stars and rewards** — opt in per member (off by default). A kid earns one
  star a day by finishing that day's preset chores; collect enough in a
  Monday–Sunday week (6 by default) and the weekly prize is earned; earn
  today's star and tablet time is allowed tomorrow.
  - Today's star updates live as items get ticked off, and is recorded for
    good at the daily reset — before completed items are cleared.
  - You can award or revoke any past day by hand, which overrides the chores;
    clear the override to hand the day back to automatic. A day with no
    preset assigned can't be earned automatically, only granted.
  - Per tracked member you get `sensor.…_stars` (state = stars this week,
    with the whole week in its attributes) and three binary sensors:
    `…_star_today`, `…_tablet_time` (today's allowance — the hook for
    whatever enforces screen time) and `…_weekly_prize`. Plus a
    `skylight_family.set_star` service.
  - Managed from the sidebar panel's **Rewards** tab: a card per kid with
    seven tappable star cells, a week pager for browsing and fixing up past
    weeks, and today's chore progress, tablet-time status and prize
    shortfall underneath.
- **A `sensor.skylight_family_<name>` per member** — the only new state
  this integration creates, carrying the mapping (`person_entity_id`,
  `calendar_entity_ids`,
  `todo_entity_id`, `color`, and a `presets` map of weekday → preset title)
  as attributes, for Skylight HA (or anything else) to read over the same
  WebSocket connection it already uses for calendar data. Actual
  events/tasks are still read straight from the real `calendar.*`/`todo.*`
  entities — this integration doesn't duplicate them.

## Why the name

`skylight` and `ha-skylight` are already taken by unrelated community
integrations for the commercial Skylight Frame product. `skylight_family` is
deliberately distinct.

## Install

Not yet published to HACS's default store. Add as a HACS custom repository:

1. HACS → Integrations → ⋮ → Custom repositories
2. Repository: `https://github.com/siesta5787/skylight-family`, Type: Integration
3. Download, then restart Home Assistant.

Or by hand: copy `custom_components/skylight_family/` into your HA config's
`custom_components/` directory and restart.

## Set up

1. **Settings → Devices & Services → Add Integration → Skylight Family.**
2. Open the entry's **Configure** page (or the ⋮ menu on the integration
   card) to add members and manage presets — each is a *subentry*, so
   Add/Edit/Remove all happen through their own dialogs.
3. After that, use the **Skylight** entry in the sidebar for day-to-day
   work — weekday routines, presets, and pushing a preset to someone now.
   It's a separate page from HA's built-in "To-do Lists"; if you'd rather
   see only one of them, hide the other by long-pressing the sidebar and
   editing it.
4. The integration's **Configure** page has two settings: the daily reset
   time, and whether the sidebar panel is **admin only** (on by default).
   Turn it off to let any Home Assistant user open the panel and change
   routines and presets from it.

## Development

This is a plain Python HA custom integration — no build step. `homeassistant`
does not run on native Windows at all (it hard-exits on launch — Linux/macOS/
WSL only), so iterate via WSL or a Linux box, with this repo's
`custom_components/skylight_family` copied or symlinked into a scratch HA
config's `config/custom_components/`. The quickest loop:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install homeassistant
mkdir -p config/custom_components
cp -r custom_components/skylight_family config/custom_components/   # or symlink if fully inside WSL's own filesystem
hass -c config
```

Then open the local HA instance at `http://localhost:8123` (reachable
directly from Windows if running under WSL2 — no port forwarding needed)
and add the integration from Settings as usual. `config/` is gitignored.
See `CLAUDE.md` for the full step-by-step (including driving it headlessly
over HA's REST API) and gotchas found while testing this.

Verified end to end against a live `homeassistant` core via WSL — config
flow, subentry flows, options flow, reconfigure and the scheduled reset job
(2026-09-13), and the sidebar panel's WebSocket API plus the panel JS itself
under jsdom (2026-10-05). See `CLAUDE.md` for exactly what was tested, what
wasn't, and the API details used to drive it headlessly.

## License

[MIT](LICENSE)
