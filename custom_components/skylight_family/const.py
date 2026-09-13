"""Constants for the Skylight Family integration."""

DOMAIN = "skylight_family"

# Member subentry fields
CONF_PERSON = "person_entity_id"
CONF_CALENDARS = "calendar_entity_ids"
CONF_TODO = "todo_entity_id"
CONF_COLOR = "color"
CONF_PRESET = "preset_subentry_id"

# Preset subentry fields
CONF_PRESET_ITEMS = "items"

# Entry options
CONF_RESET_TIME = "reset_time"
DEFAULT_RESET_TIME = "04:00:00"

SUBENTRY_TYPE_MEMBER = "member"
SUBENTRY_TYPE_PRESET = "preset"

# Seeded once on first setup, as ordinary preset subentries — not treated
# specially afterward, so editing/deleting them works the same as any
# user-created preset.
BUILTIN_PRESETS: dict[str, list[str]] = {
    "School-age kid": ["Brush teeth", "Make bed", "Pack school bag"],
    "Toddler": ["Brush teeth", "Get dressed", "Put toys away"],
    "Adult": [],
}
