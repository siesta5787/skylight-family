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
- **"Skylight" sidebar panel** (`panel.py` + `frontend/skylight-panel.js`),
  the day-to-day UI so none of this needs digging through Settings →
  Devices & Services. Two tabs: *People* (each member's 7 weekday preset
  dropdowns + Save, a "push a preset right now" row, and a collapsible
  Linked entities form for name/person/to-do/calendars/colour),
  *Presets* (view/add/edit/delete presets, plus apply-to-a-member from that
  side too) and *Rewards* (a card per tracked member: seven tappable star
  cells with their `auto`/`manual` source, a week pager, and today's chore
  progress / tablet time / prize status). Registered via
  `panel_custom.async_register_panel` with
  `component_name="custom"` and `embed_iframe=False`.
- **`panel_admin_only` option** (default on) — the integration owns this
  because HA core ships no UI that can set `require_admin` on a *custom*
  panel (see Process gotchas). It gates **both** who sees the sidebar entry
  and who may write from it: the WS mutations call
  `helpers.check_panel_write_access` instead of wearing a flat
  `@websocket_api.require_admin`, because the panel is an editor with no
  read-only mode and handing a non-admin a page where every button returns
  "Unauthorized" would be worse than not showing it at all. The read
  (`skylight_family/config`) is never gated.
  `panel.async_register(hass, admin_only=...)` stays idempotent, but when
  the flag differs from what's registered it removes and re-adds the panel
  (`panel_custom.async_register_panel` has no `update=` parameter). That's
  the only path that removes the panel outside integration removal, and it
  only runs when the user actually flips the setting.
- **The panel's own WebSocket API** (`websocket_api.py`), registered
  domain-wide in `async_setup`: `skylight_family/config` (one round trip for
  everything the panel draws — members, presets, weekday list, reset time),
  plus `preset/create`, `preset/update`, `preset/delete`, `member/update`
  and `apply_preset`. All of them are thin wrappers over
  `hass.config_entries.async_{add,update,remove}_subentry`, so the panel and
  the Settings subentry flows are two views of exactly the same data — no
  parallel storage. Mutations are `@websocket_api.require_admin`; the read
  isn't, leaving room to show a household member their routine read-only
  later.
  - `member/update` is a **merge, not a replace** — only keys present in the
    message are touched. That's what lets "Save routine" send just
    `routine` without clobbering person/calendars/colour.
  - `preset/delete` first walks every member and clears that preset id out
    of any weekday slot, so deleting can't leave a dangling reference that
    reads as "a preset is set" in the UI while the daily job applies
    nothing.
- **`helpers.py`** holds the shared lookups (`get_entry`, `members`,
  `presets`, `require_subentry`) and the to-do operations
  (`async_clear_completed`, `async_apply_items`,
  `async_apply_preset_to_member`). It exists as its own module purely so
  `websocket_api.py` and `__init__.py` can both use them without a circular
  import back through the package's `__init__`.
- **Star / reward tracking** (`rewards.py`), off per member by default
  (`rewards_enabled`, with a `star_goal` defaulting to 6). The household
  rule: one star a day for finishing that day's chores, `star_goal` stars in
  a Monday–Sunday week earns a prize, and today's star earns tablet time
  *tomorrow*. Four points matter:
  - **Stars are history, not config**, so they live in their own
    `homeassistant.helpers.storage.Store` (`.storage/skylight_family.rewards`,
    `{member_id: {"YYYY-MM-DD": {"star": bool, "source": "auto"|"manual"}}}`),
    pruned to `REWARDS_KEEP_DAYS`. Subentry data is for configuration only.
  - **Today is live, past days are frozen.** A `RewardsCoordinator`
    (`DataUpdateCoordinator`) recomputes today from the real to-do list
    whenever a watched `todo.*` entity changes, so progress shows as it
    happens. **The daily job must freeze yesterday's star *before*
    `async_clear_completed` runs** — clearing completed items destroys the
    only evidence. That ordering is the whole reason `_apply_daily_reset`
    starts with `async_freeze_day`; don't reorder it.
  - **A manual star always wins** (`source: "manual"`) until it's cleared
    back to automatic by setting it to `null`. A day with **no preset
    assigned can't be earned automatically** (nothing to measure) but can be
    granted by hand — the user's choice, so "6 of 7" stays literal. Future
    dates are refused outright, since they're always reported as "nothing
    yet" and a star set on one would be silently discarded.
  - Only items belonging to *that day's preset* count. Extra things a parent
    adds during the day are ignored rather than held against the kid, and an
    expected item that's been deleted off the list counts as done (some to-do
    integrations remove rather than complete).
- **Reward entities**, per tracked member: `sensor.…_stars` (state = stars
  this week; attributes carry the whole week as a date-keyed `days` map plus
  `goal`/`stars_needed`/`days_remaining`/`prize_earned`/`chores_done`/
  `chores_total`/`tablet_time` — this is what the wall tablet reads), and
  binary sensors `…_star_today`, `…_tablet_time` (today's allowance, i.e.
  yesterday's star — the hook for anything enforcing screen time) and
  `…_weekly_prize`. `_remove_stale_reward_entities` deletes them from the
  registry when tracking is switched off, so they don't linger as
  permanently `unavailable`; it matches `REWARD_ENTITY_SUFFIXES` rather than
  "everything that isn't the mapping sensor" so a future per-member entity
  doesn't get swept up.
- **`skylight_family.set_star`** service plus `skylight_family/rewards` and
  `skylight_family/rewards/set_star` WS commands (the latter takes an
  optional `week_start` so the panel can browse and edit history).
- No Lovelace cards and no custom theme — the sidebar panel is the only
  thing this integration renders in HA's frontend, and it's a standalone
  page, not a dashboard card. HA's built-in "To-do Lists" sidebar entry is
  untouched; hiding/reordering it is a per-user frontend preference (long-
  press the sidebar → edit), not something this integration should do.

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
- **Adding and removing *members* is still Settings-only** — the panel can
  edit everything about an existing member but can't create or delete one
  (that needs the `person`/`calendar` entity pickers and the unique-id
  handling the subentry flow already does properly). The panel says so
  where it matters.
- **The panel has not been opened in a real HA frontend yet** (see What's
  been verified, 2026-10-05) — its logic is covered by a jsdom harness and
  everything server-side is confirmed live, but the actual
  `import()`-into-HA's-shell step and the theming are unproven. That's the
  first thing to check after installing.
- The panel's styling uses HA theme CSS variables with hardcoded
  fallbacks, but it's hand-rolled CSS, not HA's own components — expect it
  to look *close to* native rather than identical, and to not pick up every
  custom theme perfectly.
- `confirm()` is used for the delete-preset prompt (browser-native dialog,
  not HA-styled). Works fine, just visibly not HA.
- **The wall tablet's star row isn't built**, and isn't being built here —
  the user's other agent owns the Skylight HA side. It needs no backend
  work: `sensor.*_stars` already carries the whole week in its attributes.
- **Rewards tab behaviour worth not "fixing" by accident**: a tap always
  writes a *manual* override (to the opposite of the day's current state),
  and manual cells grow a `reset` link that clears back to automatic —
  rather than one tap cycling through three states, which reads as
  unpredictable. Every day of a *past* week stays tappable on purpose, so a
  forgotten star can be awarded later; only genuinely future days are
  disabled. Chore progress and tablet time are hidden when browsing a past
  week, since they describe right now and would be misleading there.
- The Rewards tab tracks the current week by holding `_rewardsWeek = null`
  rather than pinning today's Monday, so the panel left open overnight rolls
  over with the clock instead of getting stuck on yesterday's week.
- Reward tracking is per member and **off by default**, which means an
  existing install sees no new entities until it's switched on in the
  member's Settings form. That's deliberate (adults don't need stars), but
  it does mean "nothing happened after updating" is the expected experience.

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

**2026-10-05**, after adding the sidebar panel, its WebSocket API and
`helpers.py`, against `homeassistant` 2026.9.2 (same WSL instance):

- **Server side, live** (`sk-wstest.py`-style script over a real authed
  WebSocket connection — mint an access token straight out of
  `config/.storage/auth`, see Dev loop step 4b):
  - The panel is registered under url_path `skylight` with
    `component_name: "custom"`, `require_admin: true`, and
    `module_url: /skylight_family_static/skylight-panel.js?v=1`; the JS
    itself is served at that path with HTTP 200.
  - `skylight_family/config` returns `configured`, all 7 weekdays, the
    reset time, and every member/preset with their real subentry ids.
  - `preset/create` strips blank/whitespace items; `preset/update` renames
    and replaces items; `preset/delete` removes it.
  - `member/update` with only `routine` saved all 7 days, and a partial
    save (`{"mon": null}`) cleared just Monday while leaving Tuesday's
    preset *and* the member's person/calendars/to-do untouched — i.e. the
    merge semantics work.
  - `apply_preset` added exactly the 2 missing items to a real
    `todo.*` entity (verified by reading the list back via
    `execute_script` → `todo.get_items`), and a second identical call
    added 0 — no duplicates.
  - Error paths return clean WS errors, not crashes: bogus member id and
    bogus preset id both come back as `home_assistant_error` with a
    specific message; a blank preset name is rejected as `invalid_format`
    by the schema.
  - `preset/delete` cleaned the deleted preset's id out of the member's
    weekday slots — no dangling references left.
  - **The panel survived all ~15 mutations**, each of which reloads the
    config entry — still registered, JS still served (see Process gotchas
    for why that needed care).
  - Nothing from `skylight_family` in the error log beyond the three
    deliberate error-path tests.
- **Panel JS, headless DOM** (jsdom harness, 54 checks, all passing —
  rendering plus every click path, asserting the exact WS message each
  button produces): first-load render, the 7 weekday selects pre-selected
  from `routine`, routine save (cleared day → `null`, changed day → preset
  id, nothing else in the payload), apply-now from both the member and the
  preset side, the guard against applying with nothing chosen, the linked-
  entities form (domain-filtered person/to-do selects, calendar checklist,
  rgb↔hex colour round trip), blank-name guards, preset create/edit/cancel/
  delete including the confirm prompt, and the not-configured / no-members /
  no-presets / load-failed empty states.
- **The `panel_admin_only` option, live, with a real non-admin user**
  (created via `config/auth/create` +
  `config/auth_provider/homeassistant/create`, then logged in through
  `/auth/login_flow` for its own token — the `skylight_test_kid` user left
  on the WSL instance is there for exactly this): with the option on, the
  non-admin couldn't see the panel in `get_panels` *and* got `unauthorized`
  on a write; after flipping it off through the real options flow, the
  same user saw the panel, could read, and could write; flipping it back on
  restored both refusals. Title, icon and `module_url` all survived the
  remove-and-re-add.
**2026-10-06**, the reward tracker, against `homeassistant` 2026.9.2 on the
same WSL instance — 38 live checks plus a separate timing test, all passing:

- Turning `rewards_enabled` on creates all four entities; turning it off
  removes them from the registry rather than leaving them `unavailable`.
- Automatic stars track the real list: 0 of 2 chores → no star, 1 of 2 →
  still no star, 2 of 2 → star with `source: "auto"`, and the
  `star_today` binary sensor follows each step.
- A manual `false` beats completed chores; clearing the override (`null`)
  restores the automatic `true`.
- `tablet_time` is on exactly when *yesterday* has a star.
- The goal: with the goal reached the `prize_earned` attribute and the
  `weekly_prize` binary sensor both flip; `stars_needed` reports the
  shortfall; `days_remaining` counts today. A full 6-of-7 week seeded
  entirely in the past reads back correctly through the WS API.
- Past weeks are browsable by `week_start`, and a week with no history comes
  back all-`null` rather than erroring.
- `set_star` refuses a future date, and refuses a member with tracking off —
  both as clean `home_assistant_error`s.
- The `skylight_family.set_star` service works targeting any of the member's
  entities, via the entity registry's `config_subentry_id`.
- **The daily job's ordering, proven against the real scheduler** (reset
  time set ~75s out through the options flow): yesterday's star was frozen
  as `{"star": true, "source": "auto"}`, the completed items were then
  cleared, today's preset was re-applied, today started with no star, and
  tablet time came on. Debug log confirms the sequence inside 26ms:
  `freezing stars for <yesterday>` → `clearing completed items from …` →
  `Cleared completed items from …` → `applying 2 item(s) to …`.

**2026-10-06 (later)**, the panel's Rewards tab:

- jsdom suite grown to **80 checks**, all passing — the week grid (7 cells,
  weekday headers in order, earned/today/disabled states), tapping an
  unstarred day awarding it, tapping a starred day taking it back, `reset`
  clearing the override to `null`, reward actions re-reading `rewards`
  rather than `config`, the week pager (Later disabled on the current week,
  Earlier asking for the previous Monday, "This week" dropping the filter),
  past weeks hiding today-only facts while staying editable, and the
  nobody-tracked empty state.
- **A second harness renders the panel against payloads captured from the
  live backend** (`sk-capture.py` → `real.mjs`), so drift between what the
  WS API sends and what the panel expects fails loudly: the rendered day
  cells' `title` attributes equal the backend's own sorted date keys, the
  `today` highlight lands on the backend's `today`, and the earned/manual
  cell counts match the payload. That's the check that would have caught a
  date-key or ordering mismatch; the fake-data suite can't.
- `PANEL_JS_VERSION` bump to `2` confirmed live: the registered panel's
  `module_url` came back as `…/skylight-panel.js?v=2`.
- The three live backend suites (38 reward checks, panel API, admin toggle)
  all still pass unchanged.

- **Not covered**: loading the panel inside a real HA frontend. The browser
  automation available in this session couldn't reach the WSL instance
  (connection refused — the automation host isn't this machine), and
  tunnelling a dev HA instance out to the internet isn't worth it. jsdom
  exercises the element's own logic but not HA's `import()`/custom-element
  handoff or theming.

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
   **4b. Getting a token on an *already*-onboarded instance** (no password
   needed, no browser): HA stores refresh tokens in plaintext in
   `config/.storage/auth`. Pick one whose user isn't `system_generated`,
   then exchange it:
   ```sh
   curl -s -X POST http://localhost:8123/auth/token \
     -d grant_type=refresh_token -d refresh_token=<the "token" field> \
     -d client_id=<that token's client_id>
   ```
   The same blob, written to `localStorage.hassTokens` as
   `{access_token, token_type:"Bearer", refresh_token, expires_in, hassUrl,
   clientId, expires}`, logs a browser straight in without the login form.
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
9. **Test the panel's WebSocket API** over a real authed connection rather
   than REST — connect to `ws://localhost:8123/api/websocket`, expect
   `auth_required`, send `{"type":"auth","access_token":...}`, expect
   `auth_ok`, then send `{"id":N,"type":"skylight_family/..."}`. `aiohttp`
   is already in the venv. Two useful extras: `{"type":"get_panels"}`
   confirms the sidebar registration and its `module_url`, and
   `{"type":"execute_script","sequence":[{"action":"todo.get_items",
   "target":{...},"response_variable":"result"},{"stop":"done",
   "response_variable":"result"}]}` reads a to-do list back over the same
   connection.
10. **Test the panel JS without a browser** with jsdom (`npm install jsdom`
    — needs `apt-get update` first on this WSL box, the cached package
    index 404s). Copy jsdom's `window`, `document`, `HTMLElement`,
    `customElements`, `CustomEvent`, `Event` and `Node` onto `globalThis`
    (**not** `navigator` — it's getter-only in Node 22 and assigning it
    throws), stub `globalThis.confirm`, `await import()` the panel file,
    then `document.createElement("skylight-family-panel")`, give it a fake
    `hass` with a recording `callWS`, and drive it by calling `.click()` on
    elements found in `el.shadowRoot`. **Keep the second harness that feeds
    it real captured payloads too** — a fake-data suite only proves the panel
    agrees with itself. Careful building dates in the fake data: use
    `Date.UTC(...)` rather than local midnight + `toISOString()`, which
    silently shifts a day east of Greenwich. Shadow DOM, `composedPath()` and
    event retargeting all work. Each click path kicks off async work, so
    await a handful of `setTimeout(…, 0)` turns before asserting (`_call`
    awaits the WS round trip *and then* a config reload).

## Process gotchas

- **Don't use the host's date in a test — ask HA.** The WSL box's local date
  and the test instance's configured timezone are hours apart, so
  `date.today()` in a test script was a full calendar day behind
  `dt_util.now().date()` inside HA, and every date-keyed assertion missed.
  Read `today` out of the `skylight_family/rewards` payload instead. (The
  HA *log* timestamps are host-local, which makes this even more confusing to
  eyeball — a job logged at 20:45 host time fired at 00:45 HA time.)
- **The HTTP API comes up long before custom components finish setting up.**
  A test that starts the moment `/api/` stops 401-ing gets
  `unknown_command` for our WS commands. Poll for one of them to succeed
  rather than polling the core API.
- **Don't poll for step one of a multi-step job and then assert on step
  four.** Two "failures" in the daily-reset test were just the script
  detecting the star freeze (first thing the job does) and immediately
  checking the to-do list and the entity states, which the job hadn't got to
  yet. Both vanished with a short sleep after the freeze is seen. Worth
  suspecting before suspecting the code — and worth checking the debug log,
  which showed the job completing cleanly all along.
- **`DataUpdateCoordinator`'s default request-refresh debouncer waits 10
  seconds before the *first* recompute** (`immediate=False`). For anything
  user-facing that's indistinguishable from broken — ticking off the last
  chore wouldn't light the star for ten seconds. Pass an explicit
  `Debouncer(..., cooldown=2.0, immediate=True)`.
- **Pass `config_entry=` to `DataUpdateCoordinator` explicitly.** Omitting it
  makes it fall back to a ContextVar and emit a frame-usage report; it's only
  ignored for custom integrations by grace. Then use `self.config_entry`
  rather than keeping a second reference.
- **There is no HA UI for toggling `require_admin` on a custom panel, and
  Settings → Dashboards is not it.** HA core *does* have a persistent,
  admin-only `frontend/update_panel` WS command that overrides a panel's
  `title`/`icon`/`require_admin`/`show_in_sidebar` (verified working
  against our panel — the override is stored separately from the panel
  registration, so it survives restarts and wins over whatever the
  integration registered). But the only page that calls it iterates a
  hardcoded allow-list:
  ```ts
  // frontend/src/panels/config/lovelace/dashboards/ha-config-lovelace-dashboards.ts
  export const PANEL_DASHBOARDS = ["home","light","security","climate","energy","maintenance"];
  ```
  so a custom panel never appears there. Don't tell the user to look in
  Settings → Dashboards (this was gotten wrong once, from seeing the WS
  command plus a `require_admin` table column and inferring a UI that
  doesn't apply to us). Hence the `panel_admin_only` option.
- **Don't tear the panel down on config-entry unload.** Every panel save
  updates a subentry → fires the entry's update listener → reloads the
  entry. If the panel were registered in `async_setup_entry` and removed in
  `async_unload_entry` (the obvious shape), the sidebar entry would
  disappear and reappear on every single save, yanking the page out from
  under the user mid-edit. So `panel.async_register` is idempotent
  (guards on `frontend.async_panel_exists`) and removal happens only in
  `async_remove_entry`, when the integration is deleted for good.
- **A static path can only be registered once per process.** aiohttp raises
  if the same route is added twice and there's no unregister, so
  `panel.py` guards `hass.http.async_register_static_paths` with a
  `hass.data` flag. Without that, the first config-entry reload would
  crash setup.
- **Bump `PANEL_JS_VERSION` in `const.py` on every edit to
  `frontend/skylight-panel.js`.** It's the `?v=` cache buster on the
  panel's `module_url`; browsers cache ES modules hard, so without a bump
  users keep running the old panel after an update even post-restart.
  (`cache_headers=False` on the static path helps but isn't sufficient on
  its own.)
- **The Android companion app needs a full swipe-close after any panel JS
  change — the cache buster is not enough.** Confirmed on the user's device
  2026-10-06 with the Rewards tab: HACS was on the right version, HA was
  serving the correct `?v=2` file, and the app still showed the two-tab
  panel. Reloading inside the app didn't help; swiping the app fully closed
  and reopening did. Likely because the companion app keeps its WebView
  alive across backgrounding, and the frontend only `import()`s
  `module_url` once per page load — so the already-imported module object
  survives in memory no matter what URL a fresh fetch would use. Tell the
  user this up front when shipping a panel change, rather than letting them
  conclude the update didn't install. (Desktop browsers just need
  Ctrl+Shift+R.)
- Diagnosing "am I running the new panel?" currently needs fetching
  `/skylight_family_static/skylight-panel.js` by hand and grepping it,
  because the panel doesn't display its own version anywhere. Worth adding a
  marker if this comes up again.
- **Decorator order on WebSocket commands**: `@require_admin` outermost,
  then `@websocket_api.websocket_command({...})`, then `@callback` (sync
  handlers) or `@websocket_api.async_response` (async ones) innermost.
  `require_admin` calls its wrapped function synchronously, so
  `async_response` has to be *inside* it. Verified that `_ws_command` /
  `_ws_schema` survive to the outermost wrapper (`functools.wraps` copies
  `__dict__`), so registration still finds them.
- Raising `HomeAssistantError` from a WS handler is enough for a clean
  client-side error — `connection.async_handle_exception` maps it to
  `home_assistant_error` with the message intact. No need for try/except
  plus `send_error` (unlike the raw REST service endpoint, which flattens
  everything to a 500 — see the `apply_preset` note in Architecture).
- **Invoking WSL from this repo's tooling: put the script in a file.**
  Inline `wsl -d Ubuntu -- bash -c '...'` is unreliable from both shells on
  this machine — PowerShell strips embedded double quotes and splits on
  spaces before `wsl.exe` sees them, and Git Bash rewrites `/absolute/paths`
  into `C:/Program Files/Git/...` (fixable with `MSYS_NO_PATHCONV=1`, but
  shell variables and `;` still get mangled). Symptom is misleading:
  variables silently expand to empty strings rather than erroring. Write
  the script to a `.sh` file and run `wsl -d Ubuntu -- bash /mnt/c/.../x.sh`.
- **`pgrep -af hass` does not match an HA started as `python -m
  homeassistant`** ("hass" isn't a substring of "homeassistant"). A stale
  instance from a previous session was holding port 8123 while appearing
  dead; the new instance started, failed to bind, and came up with *no*
  `http`/`websocket_api` component at all — while `curl localhost:8123`
  still returned 200 from the old process. Check `ss -ltnp | grep 8123`,
  not just `pgrep`. Relatedly, HA's `config/.ha_run.lock` can outlive the
  process it names; a lock whose PID isn't in `ss` output is safe to delete.
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
