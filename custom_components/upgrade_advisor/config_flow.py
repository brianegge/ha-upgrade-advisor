"""Config flow for Upgrade Advisor."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import SelectOptionDict, SelectSelector, SelectSelectorConfig

from .const import (
    CONF_AGENT_ID,
    CONF_CREATE_REPAIRS,
    CONF_DASHBOARD_PATH,
    CONF_INCLUDE_ADDONS,
    CONF_INCLUDE_AUTOMATIONS,
    CONF_POST_UPGRADE_CHECK,
    CONF_SCAN_HACS,
    CONF_SCAN_ON_UPDATE,
    DEFAULT_CREATE_REPAIRS,
    DEFAULT_DASHBOARD_PATH,
    DEFAULT_INCLUDE_ADDONS,
    DEFAULT_INCLUDE_AUTOMATIONS,
    DEFAULT_POST_UPGRADE_CHECK,
    DEFAULT_SCAN_HACS,
    DEFAULT_SCAN_ON_UPDATE,
    DOMAIN,
)


def _build_agent_selector(hass: HomeAssistant) -> SelectSelector:
    """Build a dropdown selector listing the available conversation agents."""
    from homeassistant.components.conversation import async_get_agent_info

    agent_options = []
    for state in hass.states.async_all("conversation"):
        info = async_get_agent_info(hass, state.entity_id)
        if info:
            agent_options.append(SelectOptionDict(value=info.id, label=info.name))

    if not agent_options:
        agent_options.append(SelectOptionDict(value="homeassistant", label="Home Assistant"))

    return SelectSelector(
        SelectSelectorConfig(
            options=agent_options,
            mode="dropdown",
        ),
    )


class UpgradeAdvisorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Upgrade Advisor."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step — select AI conversation agent."""
        errors: dict[str, str] = {}

        if user_input is not None:
            agent_id = user_input[CONF_AGENT_ID]

            # Validate the selected agent exists
            from homeassistant.components.conversation import async_get_agent_info

            agent_info = async_get_agent_info(self.hass, agent_id)
            if agent_info is None:
                errors["base"] = "agent_not_found"
            else:
                # Only allow one instance
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=agent_info.name,
                    data={CONF_AGENT_ID: agent_id},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_AGENT_ID): _build_agent_selector(self.hass),
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> UpgradeAdvisorOptionsFlow:
        """Get the options flow."""
        return UpgradeAdvisorOptionsFlow()


class UpgradeAdvisorOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Upgrade Advisor."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            agent_id = user_input[CONF_AGENT_ID]

            # Validate the selected agent exists
            from homeassistant.components.conversation import async_get_agent_info

            agent_info = async_get_agent_info(self.hass, agent_id)
            if agent_info is None:
                errors["base"] = "agent_not_found"
            else:
                # Keep the entry title in sync with the selected agent
                if agent_info.name != self.config_entry.title:
                    self.hass.config_entries.async_update_entry(self.config_entry, title=agent_info.name)
                return self.async_create_entry(data=user_input)

        current_agent = self.config_entry.options.get(CONF_AGENT_ID, self.config_entry.data.get(CONF_AGENT_ID))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_AGENT_ID,
                        default=current_agent,
                    ): _build_agent_selector(self.hass),
                    vol.Required(
                        CONF_SCAN_ON_UPDATE,
                        default=self.config_entry.options.get(CONF_SCAN_ON_UPDATE, DEFAULT_SCAN_ON_UPDATE),
                    ): bool,
                    vol.Required(
                        CONF_SCAN_HACS,
                        default=self.config_entry.options.get(CONF_SCAN_HACS, DEFAULT_SCAN_HACS),
                    ): bool,
                    vol.Required(
                        CONF_POST_UPGRADE_CHECK,
                        default=self.config_entry.options.get(CONF_POST_UPGRADE_CHECK, DEFAULT_POST_UPGRADE_CHECK),
                    ): bool,
                    vol.Required(
                        CONF_CREATE_REPAIRS,
                        default=self.config_entry.options.get(CONF_CREATE_REPAIRS, DEFAULT_CREATE_REPAIRS),
                    ): bool,
                    vol.Required(
                        CONF_INCLUDE_AUTOMATIONS,
                        default=self.config_entry.options.get(CONF_INCLUDE_AUTOMATIONS, DEFAULT_INCLUDE_AUTOMATIONS),
                    ): bool,
                    vol.Required(
                        CONF_INCLUDE_ADDONS,
                        default=self.config_entry.options.get(CONF_INCLUDE_ADDONS, DEFAULT_INCLUDE_ADDONS),
                    ): bool,
                    vol.Optional(
                        CONF_DASHBOARD_PATH,
                        default=self.config_entry.options.get(CONF_DASHBOARD_PATH, DEFAULT_DASHBOARD_PATH),
                    ): str,
                }
            ),
            errors=errors,
        )
