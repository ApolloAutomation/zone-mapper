"""
Config and options flow for Zone Mapper.

The integration is UI-configurable and has a single setup step that just
creates the singleton entry. The options flow exposes a single toggle for the
first-run auto-view seeding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_AUTO_CREATE_VIEW,
    DEFAULT_AUTO_CREATE_VIEW,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry


class ZoneMapperConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow to create a single Zone Mapper entry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show a confirmation form and create the entry when submitted."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title="Zone Mapper", data={})

        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> ZoneMapperOptionsFlow:
        """Return the options flow for this entry."""
        return ZoneMapperOptionsFlow(config_entry)


class ZoneMapperOptionsFlow(config_entries.OptionsFlow):
    """Options flow with a single toggle for auto-view seeding."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Store the entry the options apply to."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Prompt for the auto-view toggle and save it."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._config_entry.options.get(
            CONF_AUTO_CREATE_VIEW, DEFAULT_AUTO_CREATE_VIEW
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_AUTO_CREATE_VIEW, default=current): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
