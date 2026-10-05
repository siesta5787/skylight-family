"""Constants for the Skylight Family integration."""

DOMAIN = "skylight_family"

# Member subentry fields
CONF_PERSON = "person_entity_id"
CONF_CALENDARS = "calendar_entity_ids"
CONF_TODO = "todo_entity_id"
CONF_COLOR = "color"

# Per-weekday preset assignment: (key, display label), Monday first to match
# Python's own date.weekday() numbering (Monday == 0). A member's subentry
# stores one optional preset_subentry_id per day under WEEKDAY_PRESET_FIELDS[key]
# — no assignment for a given day means no preset is applied that day.
WEEKDAYS: list[tuple[str, str]] = [
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
]
WEEKDAY_PRESET_FIELDS: dict[str, str] = {key: f"preset_{key}" for key, _ in WEEKDAYS}

# Preset subentry fields
CONF_PRESET_ITEMS = "items"

# Entry options
CONF_RESET_TIME = "reset_time"
DEFAULT_RESET_TIME = "04:00:00"
# Whether the sidebar panel is admin-only. HA core has no UI that can toggle
# this for a custom panel — Settings -> Dashboards only lists six hardcoded
# built-in panels (frontend's PANEL_DASHBOARDS) — so we own the setting.
# It gates both who sees the sidebar entry and who may change anything from
# it, since the panel is an editor with no read-only mode.
CONF_PANEL_ADMIN_ONLY = "panel_admin_only"
DEFAULT_PANEL_ADMIN_ONLY = True

# Service: skylight_family.apply_preset
SERVICE_APPLY_PRESET = "apply_preset"
ATTR_PRESET = "preset"

SUBENTRY_TYPE_MEMBER = "member"
SUBENTRY_TYPE_PRESET = "preset"

# Sidebar panel — the "Skylight" entry in HA's own sidebar, served straight
# out of this integration's frontend/ directory (no build step).
PANEL_URL_PATH = "skylight"
PANEL_TITLE = "Skylight"
PANEL_ICON = "mdi:white-balance-sunny"
PANEL_ELEMENT = "skylight-family-panel"
PANEL_STATIC_URL = "/skylight_family_static"
PANEL_FILENAME = "skylight-panel.js"
# Cache buster appended to the panel's module_url. Browsers cache ES modules
# aggressively; bump this on every edit to frontend/skylight-panel.js or
# users keep getting the old panel after an update.
PANEL_JS_VERSION = "1"

# Seeded once on first setup, as ordinary preset subentries — not treated
# specially afterward, so editing/deleting them works the same as any
# user-created preset.
BUILTIN_PRESETS: dict[str, list[str]] = {
    "School-age kid": ["Brush teeth", "Make bed", "Pack school bag"],
    "Toddler": ["Brush teeth", "Get dressed", "Put toys away"],
    "Adult": [],
}
