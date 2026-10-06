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

# Reward tracking, per member. Off by default so adults don't get star
# entities they'll never use.
CONF_REWARDS_ENABLED = "rewards_enabled"
CONF_STAR_GOAL = "star_goal"
DEFAULT_STAR_GOAL = 6

# Stars are history, not configuration, so they live in their own Store
# rather than in subentry data.
STORAGE_KEY = f"{DOMAIN}.rewards"
STORAGE_VERSION = 1
# How much history to keep. Enough for the panel to browse back a couple of
# months; anything older gets pruned so the store stays small.
REWARDS_KEEP_DAYS = 70

# How a day's star was decided. A manual entry always wins over the
# automatic result and stays put until it's cleared again.
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"

# unique_id suffixes of the entities that only exist while reward tracking is
# on, so they can be cleaned out of the registry when it's turned off.
REWARD_ENTITY_SUFFIXES = ("stars", "star_today", "tablet_time", "weekly_prize")

# Pocket-money tracking, per member. Like rewards, off by default.
CONF_MONEY_ENABLED = "money_enabled"
# Annual percentage, divided by 52 and credited every Monday. Stored as a
# plain number (3.0 means 3%); only the long-term account earns it.
CONF_INTEREST_RATE = "interest_rate"
DEFAULT_INTEREST_RATE = 0.0
WEEKS_PER_YEAR = 52

MONEY_STORAGE_KEY = f"{DOMAIN}.money"
MONEY_STORAGE_VERSION = 1

ACCOUNT_SHORT = "short"
ACCOUNT_LONG = "long"
ACCOUNTS = (ACCOUNT_SHORT, ACCOUNT_LONG)
ACCOUNT_LABELS = {ACCOUNT_SHORT: "Short term", ACCOUNT_LONG: "Long term"}

KIND_DEPOSIT = "deposit"
KIND_EXPENSE = "expense"
# A transfer is a single entry, not a paired expense+deposit: one id to edit
# or delete, and no way to end up with half a transfer. `account` is the
# source, `to_account` the destination, and replay applies it to both.
KIND_TRANSFER = "transfer"
ENTRY_KINDS = (KIND_DEPOSIT, KIND_EXPENSE, KIND_TRANSFER)
# Interest is always derived by replaying the ledger, never stored — that's
# what makes back-dating an entry correctly reshape the interest after it.
KIND_INTEREST = "interest"

MONEY_ENTITY_SUFFIXES = ("short_term", "long_term")

# Service: skylight_family.add_money
SERVICE_ADD_MONEY = "add_money"
ATTR_ACCOUNT = "account"
ATTR_AMOUNT = "amount"
ATTR_KIND = "kind"
ATTR_NOTE = "note"
ATTR_TO_ACCOUNT = "to_account"

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

# Service: skylight_family.set_star
SERVICE_SET_STAR = "set_star"
ATTR_DATE = "date"
ATTR_STAR = "star"

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
PANEL_JS_VERSION = "4"

# Seeded once on first setup, as ordinary preset subentries — not treated
# specially afterward, so editing/deleting them works the same as any
# user-created preset.
BUILTIN_PRESETS: dict[str, list[str]] = {
    "School-age kid": ["Brush teeth", "Make bed", "Pack school bag"],
    "Toddler": ["Brush teeth", "Get dressed", "Put toys away"],
    "Adult": [],
}
