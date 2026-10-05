# Skylight Family — project notes

A Home Assistant custom integration (`custom_components/skylight_family`)
that coordinates family members — linked to existing HA `person.*` entities —
with their `calendar.*`/`todo.*` entities and reusable daily-reset task
presets, entirely through HA's Settings UI (no YAML). It's the "backend"
half of a two-repo project; the "frontend"/display half is
[`skylight-ha`](https://github.com/siesta5787/skylight-ha) (a separate Rust/
Slint wall-tablet app, lives in `../Skylight HA` on this machine), which
reads this integration's output over its existing HA WebSocket connection.

This repo is plain Python — no build step, no Buildroot, no cross-compiling.
Full design rationale and the decisions behind the current shape are in
`skylight-ha`'s Claude memory (`project_skylight_family_integration.md`);
this file is the day-to-day working notes for *this* repo.

## Naming

Domain is `skylight_family`, deliberately distinct from `skylight` /
`ha-skylight`, which are already taken by unrelated third-party integrations
for the commercial Skylight Frame product (dknowles2/ha-skylight,
MegaTheLEGEND/skylight_calendar, etc.). Don't rename the domain later —
it's baked into `manifest.json` and every entity ID once anyone's installed
it.

## Architecture

- **One config entry** ("Skylight Family"), created via `config_flow.py`'s
  `SkylightFamilyConfigFlow`. Only one instance allowed.
- **Members and presets are config *subentries*** hung off that entry
  (`MemberSubentryFlow`, `PresetSubentryFlow`) — HA's newer per-child-item
  pattern (same idea as Ollama's per-model entries), chosen specifically so
  Add/Edit/Remove all get native dialogs for free instead of a hand-rolled
  list in a classic `OptionsFlow`.
- **Presets are seeded once** on first setup (`_seed_builtin_presets` in
  `__init__.py`) — School-age kid / Toddler / Adult — then become ordinary
  editable/deletable preset subentries, not special-cased afterward.
- **Per-weekday preset assignment, not one preset per member.** A member
  subentry stores up to 7 independent fields, `preset_mon`..`preset_sun`
  (`WEEKDAY_PRESET_FIELDS` in `const.py`), each an optional preset
  `subentry_id`. No assignment for a day means no preset applies that day —
  these are independent slots, not a single preset with day overrides, so a
  "same chores every day" member needs the same preset picked all 7 times.
- **Daily job** (`_apply_daily_reset`, `async_track_time_change`, default
  04:00), for every member with a `todo_entity_id`: first clears completed
  items via the stock `todo.remove_completed_items` service
  (`_clear_completed_items`), then — if today's weekday has a preset
  assigned — reads existing items via `todo.get_items` and adds back any
  preset item summary not already present via `todo.add_item`
  (`_reset_todo_list`). Doesn't touch calendars or create its own storage —
  it only ever drives HA's real todo entities. "Today" is `dt_util.now()`,
  i.e. HA's configured timezone, matching when the scheduled trigger fires.
- **On-demand apply**: `skylight_family.apply_preset` service
  (`services.yaml`, handler in `__init__.py`'s `async_setup`) — push a named
  preset onto a targeted member's to-do list right now, independent of the
  schedule. Target is the member's `sensor.skylight_family_<name>` entity
  (resolved to its subentry via the entity registry's `config_subentry_id`,
  not by parsing the entity_id/name); `preset` field matches an existing
  preset's title case-insensitively. Raises `HomeAssistantError` with a
  plain-English message for an unknown preset or a non-member target entity
  — shows as a toast via the frontend/WebSocket call path, but note the raw
  REST `/api/services/...` endpoint surfaces *any* service-raised
  `HomeAssistantError` as a bare 500 rather than a clean 4xx (a HA REST API
  quirk, not specific to us — don't mistake that 500 for our error handling
  being wrong when testing over REST).
- **One `sensor.skylight_family_<name>` per member** (`sensor.py`) is the
  only new state this integration creates. State value is meaningless
  ("ok"); the payload is in `extra_state_attributes`: `person_entity_id`,
  `calendar_entity_ids`, `todo_entity_id`, `color`, and `presets` — a dict
  of all 7 weekday keys to the assigned preset's title (or `null`). This is
  what `skylight-ha` is meant to read to build its member list — everything
  else (actual events/tasks) it reads straight from the real
  `calendar.*`/`todo.*` entities, not duplicated here.
- No Lovelace cards, no custom theme, no streaks/celebrations — nothing
  renders in HA's own frontend at all. (A custom sidebar panel for
  viewing/editing presets and manually applying them is a planned separate
  frontend project, not started — see
  `project_skylight_family_integration.md` memory for that discussion.)

## Known gaps

- **`CONF_COLOR` is an RGB list** (`[r, g, b]`, from `ColorRGBSelector`),
  not a hex string — fine functionally, just don't assume `"#rrggbb"` when
  wiring up the Skylight HA side.
- Schema changed 2026-10-04 (single `preset_subentry_id` → 7 per-weekday
  fields) with no migration — any member subentry created before that date
  still has the old field sitting in its data, harmlessly ignored by
  current code (nothing reads `preset_subentry_id` anymore). Not a problem
  pre-release; would need a migration if this were ever in real use before
  the schema change.
- Not yet exercised: `PresetSubentryFlow.async_step_reconfigure`
  specifically (structurally identical to the member one, which is
  verified), and member *removal* (unloading a subentry / cleanup path).
- No custom sidebar panel yet for viewing/editing presets or manually
  applying them without going through Settings → Devices & Services — the
  `apply_preset` service exists but today it's only reachable via
  Developer Tools → Actions, an automation, or the WebSocket/REST API.

## What's been verified

**2026-09-13**, against `homeassistant` 2026.2.3 then 2026.9.2 — tested
end-to-end via a scratch WSL instance, driving the whole flow headlessly
over HA's REST API:

- Main config flow creates the single entry and seeds all three built-in
  presets automatically.
- `MemberSubentryFlow`'s `user` step schema is correct: `person`/`calendar`/
  `todo` `EntitySelector`s scoped to the right domains, `ColorRGBSelector`,
  and the preset `SelectSelector` correctly lists live preset subentries by
  their real `subentry_id`/title.
- Creating a member produces `sensor.skylight_family_<name>` with the
  expected attributes.
- The options flow (`reset_time`) round-trips correctly, including showing
  the previously-saved value as the new default on reopen.
- `entry.add_update_listener` reload-on-change works for both an options
  change and a subentry add — the entry stays in `state: loaded` and the
  member sensor survives.
- `async_step_reconfigure` pre-fills every field with the subentry's
  current values and `async_update_and_abort` correctly updates them.
- The daily job's assumptions about the stock `todo` integration are
  correct: `todo.get_items` (with `return_response=True`) returns
  `{entity_id: {"items": [{"summary": ..., "uid": ..., "status": ...}]}}`,
  and `todo.add_item` accepts `{"item": "<text>"}`.
- End-to-end: with a member on a preset and one of its items already on
  the to-do list, triggering the scheduled reset added exactly the missing
  items and did not duplicate the existing one.

**2026-10-04**, after adding per-weekday presets, the `apply_preset`
service, and daily auto-clear, against `homeassistant` 2026.9.2 (same WSL
instance, survived three weeks untouched — venv and `.storage` both intact):

- The old single-member-preset data on pre-existing test members
  (`preset_subentry_id`) was silently ignored after the upgrade with no
  crash — entry reloaded to `state: loaded` cleanly.
- `MemberSubentryFlow`'s reconfigure form correctly renders all 7
  `preset_mon`..`preset_sun` `SelectSelector`s, each independently
  optional. **Found and fixed a real bug here**: submitting a reconfigure
  that omitted several of the unset weekday fields crashed with
  `"expected str"` validation errors — see Process gotchas. The same
  latent bug existed for `CONF_TODO`/`CONF_COLOR` in the reconfigure step
  (fixed at the same time via a shared `_optional_key` helper) but hadn't
  been triggered by prior testing, which always submitted every field.
- After the fix: setting Monday/Tuesday presets and leaving the rest unset
  saved correctly, and `sensor.skylight_family_<name>`'s `presets`
  attribute showed the right title per day and `null` for unset days.
- The combined daily job: pre-seeded two completed items and three
  needs_action items on a member's to-do list, triggered the scheduled
  job, and confirmed both halves in one pass — the two completed items
  were removed via `todo.remove_completed_items`, and today's
  weekday-assigned preset's items (already present) were left untouched/
  not duplicated.
- `skylight_family.apply_preset`: calling it against a member's sensor
  entity with an empty to-do list and an existing preset name pushed
  exactly that preset's items. Confirmed clean error messages (verified in
  the log, not just assumed) for both an unknown preset name and a
  non-member target entity — `HomeAssistantError` with a specific message
  in both cases, not a crash.

## Dev loop / testing

**Native Windows does not work at all** — confirmed 2026-09-13, not just a
"rougher edges" guess. `homeassistant` installs fine via `pip` on bare
Windows, but on launch it hard-exits with `Home Assistant only supports
Linux, OSX and Windows using WSL` (an explicit platform check in HA core,
not a missing-dependency problem). **Use WSL** (confirmed working — this
machine's `Ubuntu` WSL2 distro, Python 3.14.4 via apt, was what all the
2026-09-13 testing above actually ran on) or the Pop!_OS machine.

1. **Set up a venv inside WSL** (native WSL filesystem, e.g. `~/`, not
   `/mnt/c/...` — avoids DrvFs symlink/performance quirks):
   ```sh
   wsl -d Ubuntu -- bash -c "python3 -m venv ~/skylight-family-test/.venv"
   ```
   (Needs `python3.<minor>-venv` from apt first if you get an "ensurepip is
   not available" error — `sudo apt-get install -y python3.14-venv`, minor
   version matching whatever `python3 --version` reports.)
2. **Copy the integration in** (`config/` is gitignored; symlinking works
   too if you're fully inside WSL's own filesystem, unlike across the
   `/mnt/c` boundary):
   ```sh
   mkdir -p ~/skylight-family-test/config/custom_components
   cp -r custom_components/skylight_family ~/skylight-family-test/config/custom_components/
   ```
3. **Install and launch** — `pip install homeassistant` always resolves the
   latest release regardless of host Python version (pulled 2026.2.3 on
   Windows Python 3.13, 2026.9.2 on WSL Python 3.14 — don't read anything
   into the version number, it's just "whatever's newest that day"):
   ```sh
   ~/skylight-family-test/.venv/bin/pip install homeassistant
   ~/skylight-family-test/.venv/bin/python -m homeassistant -c ~/skylight-family-test/config
   ```
   Launch this via a tool call with backgrounding tied to that *specific*
   invocation (e.g. Claude Code's `run_in_background`), not `nohup ... &
   disown` from inside a `wsl.exe -d Ubuntu -- bash -c "..."` one-shot
   call — WSL2's lightweight VM can tear down shortly after the invoking
   `wsl.exe` process exits even with `disown`, killing the "detached"
   process anyway. A command that itself becomes the tracked background
   task keeps the whole `wsl.exe` process (and the WSL session under it)
   alive for as long as needed.
   `http://localhost:8123` is reachable directly from Windows — WSL2 proxies
   the port automatically, no manual forwarding needed. First boot is slow
   (~5 min): `default_config` tries to pull in a long tail of optional
   integrations (assist/voice, bluetooth, cloud, ffmpeg) that fail to build
   or lack permissions in a plain WSL container — that's all expected noise,
   harmless, and unrelated to this integration. Grep the log for
   `skylight_family` specifically rather than reading top-to-bottom.
4. **Onboarding is scriptable** — useful for redoing this quickly, since it
   doesn't need a browser:
   ```sh
   curl -s -X POST http://localhost:8123/api/onboarding/users -H "Content-Type: application/json" \
     -d '{"client_id":"http://localhost:8123/","name":"Test Admin","username":"admin","password":"...","language":"en"}'
   # -> {"auth_code": "..."}
   curl -s -X POST http://localhost:8123/auth/token \
     -d grant_type=authorization_code -d code=<auth_code> -d client_id=http://localhost:8123/
   # -> access_token; then POST empty {} to /api/onboarding/core_config and
   # /api/onboarding/analytics with that Bearer token, then POST
   # {"client_id":...,"redirect_uri":...} to /api/onboarding/integration for
   # a final auth_code, exchange that the same way for the real session token.
   ```
5. **Create test entities** the member form's `EntitySelector`s need (a
   fresh instance has none) — People → Add Person, and Settings → Devices
   & Services → Add Integration → **Local Calendar** / **Local To-do
   List** (or the REST equivalents — `POST /api/config/config_entries/flow`
   with `{"handler": "local_calendar", ...}` etc.).
6. **Drive the integration's flows** either from the frontend, or headlessly
   over REST (all confirmed working 2026-09-13 — note the exact body
   shapes below, since they're easy to get wrong):
   - Main flow: `POST /api/config/config_entries/flow` with
     `{"handler": "skylight_family", ...}`, then `POST .../flow/<flow_id>`
     with `{}`.
   - **Subentry flows** (add a member/preset): `POST
     /api/config/config_entries/subentries/flow` with `{"handler":
     [<entry_id>, "member"|"preset"], ...}` — `handler[0]` is the config
     **entry_id**, not the domain string (posting the domain there raises
     `UnknownEntry`).
   - **Subentry reconfigure**: same URL, same `handler`, plus a top-level
     `"subentry_id": <subentry_id>` field — *not* reusing `entry_id` for
     it. This is what actually routes to `async_step_reconfigure` (`step_id`
     comes back `"reconfigure"` instead of `"user"`, and every field is
     pre-filled).
   - **Options flow**: `POST /api/config/config_entries/options/flow` with
     `{"handler": <entry_id>, ...}` (bare entry_id here, not a list).
   - Subentry IDs aren't exposed over REST list endpoints — easiest way to
     find one during testing is reading `config/.storage/core.config_entries`
     directly (JSON, has every subentry's id/type/title/data).
7. **Check the sensor**: `GET /api/states/sensor.skylight_family_<name>`
   (or Developer Tools → States) → confirm `attributes` match what was
   picked, including `preset` resolving to the preset's title.
8. **Check the daily reset logic** without waiting for 04:00: submit the
   options flow with a reset time a couple minutes out, then poll
   `POST /api/services/todo/get_items?return_response` with
   `{"entity_id": "<todo_entity_id>"}` around that time — missing preset
   items should appear, already-present ones shouldn't duplicate.

## Process gotchas

- **HA core refuses to run on native Windows, full stop** (see Dev loop) —
  this isn't a "no Python" problem, it's a hard platform check in HA
  itself. Always test via WSL or Pop!_OS, mirroring the same two-machine
  split `skylight-ha` already uses for anything that needs a real Linux
  environment. (Python 3.13 *does* install and run fine standalone on this
  Windows machine via the official python.org installer if ever needed for
  something HA-unrelated — it's specifically `homeassistant` that refuses
  the platform.)
- **Callbacks passed to `async_track_time_change`/similar HA event helpers
  must be decorated `@homeassistant.core.callback`.** A bare lambda (or
  any undecorated function) gets classified as an "executor" job by HA's
  job-type inference and is dispatched to a worker thread — and then
  calling `hass.async_create_task` from inside it raises `RuntimeError:
  ... calls hass.async_create_task from a thread other than the event
  loop`. Hit this for real in `_handle_reset_time` (`__init__.py`) on
  2026-09-13: the scheduled reset fired correctly but crashed before doing
  anything, until the lambda was replaced with a small `@callback`-decorated
  function. Same rule applies to any other bare callback handed to an HA
  event-tracking helper in this codebase.
- **A `vol.Optional(key, default=value)` still runs `value` through the
  field's selector/validator when `key` is omitted from input — it doesn't
  just skip the field.** If `value` is `None` (e.g. "this optional field
  has no previous value") and the field's selector is something like
  `SelectSelector`/`EntitySelector` that doesn't accept `None`, voluptuous
  substitutes `None` in and then fails validating it, surfacing as a
  confusing `"expected str"`-style error that looks like a frontend/input
  problem rather than a schema-construction bug. Fix: only attach a
  `default=` when there's a real previous value; otherwise use a bare
  `vol.Optional(key)` so an omitted field is skipped entirely (see
  `_optional_key` in `config_flow.py`). Hit this for real 2026-10-04 adding
  the 7 per-weekday preset fields — and it turned out to be a *latent* bug
  in the original `CONF_TODO`/`CONF_COLOR` reconfigure fields too, just
  never triggered because earlier manual testing always submitted every
  field rather than omitting untouched optional ones (which is what HA's
  real frontend does for an untouched empty select).
- `gh repo create` for this project's repo used `--private`, matching
  `skylight-ha`'s visibility — keep that consistent if either repo's
  visibility changes.
