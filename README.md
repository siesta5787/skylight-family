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
  "School-age kid": brush teeth, make bed, pack school bag). Assign a preset
  to a member and its items get re-added to their to-do list every morning
  (default 04:00) if missing — so routines don't have to be manually reset.
  Three presets (School-age kid, Toddler, Adult) are seeded on first setup;
  edit or delete them like any other preset.
- **Nothing new to look at in HA itself** — no Lovelace cards, no theme.
  The integration creates one `sensor.skylight_family_<name>` per member
  carrying the mapping (`person_entity_id`, `calendar_entity_ids`,
  `todo_entity_id`, `color`, `preset`) as attributes, for Skylight HA (or
  anything else) to read over the same WebSocket connection it already uses
  for calendar data. Actual events/tasks are still read straight from the
  real `calendar.*`/`todo.*` entities — this integration doesn't duplicate
  them.

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

Verified end to end against a live `homeassistant` core via WSL on
2026-09-13 (config flow, subentry flows, options flow, reconfigure, and the
scheduled reset job all confirmed working) — see `CLAUDE.md` for exactly
what was tested and the REST API details used to drive it headlessly.

## License

[MIT](LICENSE)
