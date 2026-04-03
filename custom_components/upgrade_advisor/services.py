"""Service actions for Upgrade Advisor."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


def async_register_services(hass: HomeAssistant) -> None:
    """Register Upgrade Advisor services."""

    async def handle_analyze(call: ServiceCall) -> None:
        """Handle the analyze service call."""
        for _entry_id, data in hass.data[DOMAIN].items():
            coordinator = data.get("coordinator")
            if coordinator is not None:
                await coordinator.async_analyze_available_update()
                return
        raise HomeAssistantError("Upgrade Advisor is not configured")

    async def handle_analyze_version(call: ServiceCall) -> None:
        """Handle the analyze_version service call."""
        version = call.data["version"]
        for _entry_id, data in hass.data[DOMAIN].items():
            coordinator = data.get("coordinator")
            if coordinator is not None:
                await coordinator.async_analyze_version(version)
                return
        raise HomeAssistantError("Upgrade Advisor is not configured")

    hass.services.async_register(DOMAIN, "analyze", handle_analyze)
    hass.services.async_register(
        DOMAIN,
        "analyze_version",
        handle_analyze_version,
        schema=vol.Schema({vol.Required("version"): str}),
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    """Unregister Upgrade Advisor services."""
    hass.services.async_remove(DOMAIN, "analyze")
    hass.services.async_remove(DOMAIN, "analyze_version")
