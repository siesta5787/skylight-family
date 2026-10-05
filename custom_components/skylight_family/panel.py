"""Registers the "Skylight" sidebar panel.

The panel is a single hand-written ES module in `frontend/` — no build step,
no npm, so it can ship inside the integration and be installed by HACS like
any other custom component.

Registration deliberately survives a config-entry reload. Saving anything
from the panel updates a subentry, which triggers the entry's update
listener and reloads the entry; if the panel were torn down and re-added on
every reload, the frontend would see the sidebar item vanish out from under
the user mid-edit. So the panel is registered once and only removed when the
integration itself is removed.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import (
    DOMAIN,
    PANEL_ELEMENT,
    PANEL_FILENAME,
    PANEL_ICON,
    PANEL_JS_VERSION,
    PANEL_STATIC_URL,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

_STATIC_REGISTERED = f"{DOMAIN}_static_registered"


async def async_register(hass: HomeAssistant) -> None:
    """Serve the panel's JS and add the sidebar entry. Safe to call twice."""
    if not hass.data.get(_STATIC_REGISTERED):
        frontend_dir = Path(__file__).parent / "frontend"
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_STATIC_URL,
                    str(frontend_dir),
                    # The version query string below is the real cache
                    # buster; no caching keeps dev iteration sane too.
                    cache_headers=False,
                )
            ]
        )
        # aiohttp raises if the same route is registered twice, and static
        # paths can't be unregistered, so this is a one-shot for the life of
        # the process.
        hass.data[_STATIC_REGISTERED] = True

    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        return

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_STATIC_URL}/{PANEL_FILENAME}?v={PANEL_JS_VERSION}",
        embed_iframe=False,
        require_admin=True,
    )
    _LOGGER.debug("Registered the %s sidebar panel", PANEL_URL_PATH)


@callback
def async_remove(hass: HomeAssistant) -> None:
    """Drop the sidebar entry — only on integration removal, not reload."""
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
