"""Tests for Upgrade Advisor services."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor.const import DOMAIN

from .conftest import MOCK_CONFIG


async def test_analyze_service_registered(hass: HomeAssistant) -> None:
    """Test that the analyze service is registered on setup."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "analyze")
    assert hass.services.has_service(DOMAIN, "analyze_version")


async def test_analyze_service_calls_coordinator(hass: HomeAssistant) -> None:
    """Test that the analyze service delegates to the coordinator."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.async_analyze_available_update = AsyncMock()

    await hass.services.async_call(DOMAIN, "analyze", blocking=True)

    coordinator.async_analyze_available_update.assert_called_once()


async def test_analyze_version_service(hass: HomeAssistant) -> None:
    """Test the analyze_version service passes the version."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.async_analyze_version = AsyncMock()

    await hass.services.async_call(DOMAIN, "analyze_version", {"version": "2024.12.0"}, blocking=True)

    coordinator.async_analyze_version.assert_called_once_with("2024.12.0")
