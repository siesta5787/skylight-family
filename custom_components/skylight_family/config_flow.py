"""Config flow for Skylight Family.

Everything here is UI-driven (Settings -> Devices & Services): a single main
entry ("Skylight Family"), plus two kinds of subentries hung off it — one
per family member, and one per reusable task preset. No YAML.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CALENDARS,
    CONF_COLOR,
    CONF_PERSON,
    CONF_PRESET,
    CONF_PRESET_ITEMS,
    CONF_RESET_TIME,
    CONF_TODO,
    DEFAULT_RESET_TIME,
    DOMAIN,
    SUBENTRY_TYPE_MEMBER,
    SUBENTRY_TYPE_PRESET,
)


class SkylightFamilyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the single Skylight Family hub entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Skylight Family", data={})
        return self.async_show_form(step_id="user")

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_TYPE_MEMBER: MemberSubentryFlow,
            SUBENTRY_TYPE_PRESET: PresetSubentryFlow,
        }

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return SkylightFamilyOptionsFlow()


class SkylightFamilyOptionsFlow(OptionsFlow):
    """Integration-wide settings — currently just the daily preset reset time."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RESET_TIME,
                    default=self.config_entry.options.get(
                        CONF_RESET_TIME, DEFAULT_RESET_TIME
                    ),
                ): selector.TimeSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


def _preset_choices(entry: ConfigEntry) -> dict[str, str]:
    """subentry_id -> title, for every preset subentry on this entry."""
    return {
        subentry.subentry_id: subentry.title
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_TYPE_PRESET
    }


class MemberSubentryFlow(ConfigSubentryFlow):
    """Add / edit a single family member."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        entry = self._get_entry()

        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME], data=user_input
            )

        preset_choices = _preset_choices(entry)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME): selector.TextSelector(),
            vol.Required(CONF_PERSON): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="person")
            ),
            vol.Required(CONF_CALENDARS): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            vol.Optional(CONF_TODO): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo")
            ),
            vol.Optional(CONF_COLOR): selector.ColorRGBSelector(),
        }
        if preset_choices:
            schema_dict[vol.Optional(CONF_PRESET)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": subentry_id, "label": title}
                        for subentry_id, title in preset_choices.items()
                    ]
                )
            )

        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(schema_dict)
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(), subentry, title=user_input[CONF_NAME], data=user_input
            )

        entry = self._get_entry()
        preset_choices = _preset_choices(entry)

        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=subentry.data.get(CONF_NAME)): selector.TextSelector(),
            vol.Required(
                CONF_PERSON, default=subentry.data.get(CONF_PERSON)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="person")),
            vol.Required(
                CONF_CALENDARS, default=subentry.data.get(CONF_CALENDARS, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="calendar", multiple=True)
            ),
            vol.Optional(
                CONF_TODO, default=subentry.data.get(CONF_TODO)
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="todo")),
            vol.Optional(
                CONF_COLOR, default=subentry.data.get(CONF_COLOR)
            ): selector.ColorRGBSelector(),
        }
        if preset_choices:
            schema_dict[
                vol.Optional(CONF_PRESET, default=subentry.data.get(CONF_PRESET))
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": subentry_id, "label": title}
                        for subentry_id, title in preset_choices.items()
                    ]
                )
            )

        return self.async_show_form(
            step_id="reconfigure", data_schema=vol.Schema(schema_dict)
        )


class PresetSubentryFlow(ConfigSubentryFlow):
    """Add / edit a reusable task preset (a named list of default to-do items)."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_PRESET_ITEMS: user_input.get(CONF_PRESET_ITEMS, [])},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): selector.TextSelector(),
                vol.Optional(CONF_PRESET_ITEMS): selector.TextSelector(
                    selector.TextSelectorConfig(multiple=True)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()

        if user_input is not None:
            return self.async_update_and_abort(
                self._get_entry(),
                subentry,
                title=user_input[CONF_NAME],
                data={CONF_PRESET_ITEMS: user_input.get(CONF_PRESET_ITEMS, [])},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=subentry.title): selector.TextSelector(),
                vol.Optional(
                    CONF_PRESET_ITEMS, default=subentry.data.get(CONF_PRESET_ITEMS, [])
                ): selector.TextSelector(selector.TextSelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema)
