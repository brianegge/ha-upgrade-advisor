"""Tests for the summarize module."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor.summarize import (
    _format_entity_domains,
    _get_model_name,
    async_get_automation_summaries,
    async_get_integration_list,
    build_installation_context,
)


def test_format_entity_domains_single() -> None:
    """Test formatting a single entity domain."""
    from collections import Counter

    domains = Counter({"light": 1})
    assert _format_entity_domains(domains) == "light"


def test_format_entity_domains_multiple() -> None:
    """Test formatting multiple entity domains with counts."""
    from collections import Counter

    domains = Counter({"light": 1, "sensor": 3, "binary_sensor": 2})
    result = _format_entity_domains(domains)
    assert "light" in result
    assert "sensor x3" in result
    assert "binary_sensor x2" in result


def test_get_model_name_with_manufacturer_and_model() -> None:
    """Test model name with both manufacturer and model."""
    device = MagicMock()
    device.manufacturer = "Philips"
    device.model = "Extended Color Light"
    device.name = "Living Room Light"
    assert _get_model_name(device) == "Philips Extended Color Light"


def test_get_model_name_model_only() -> None:
    """Test model name with only model."""
    device = MagicMock()
    device.manufacturer = None
    device.model = "ZEN77 Dimmer"
    device.name = "Hallway Dimmer"
    assert _get_model_name(device) == "ZEN77 Dimmer"


def test_get_model_name_fallback_to_name() -> None:
    """Test model name fallback to device name."""
    device = MagicMock()
    device.manufacturer = None
    device.model = None
    device.name = "My Device"
    assert _get_model_name(device) == "My Device"


def test_get_model_name_unknown() -> None:
    """Test model name when nothing is available."""
    device = MagicMock()
    device.manufacturer = None
    device.model = None
    device.name = None
    assert _get_model_name(device) == "Unknown device"


async def test_get_automation_summaries_empty(hass: HomeAssistant) -> None:
    """Test automation summaries with no automations."""
    result = async_get_automation_summaries(hass)
    assert result == "No automations configured."


async def test_get_integration_list(hass: HomeAssistant) -> None:
    """Test integration list with entries."""
    # The integration list reads from config entries which are empty in test
    result = async_get_integration_list(hass)
    # With no entries, returns empty
    assert isinstance(result, str)


async def test_build_installation_context(hass: HomeAssistant) -> None:
    """Test building full installation context."""
    context = build_installation_context(hass, include_automations=True, include_addons=True)
    assert "integrations" in context
    assert "devices" in context
    assert "automations" in context
    assert "addons" in context


async def test_build_installation_context_excluded(hass: HomeAssistant) -> None:
    """Test building context with exclusions."""
    context = build_installation_context(hass, include_automations=False, include_addons=False)
    assert context["automations"] == "Automations excluded from analysis."
    assert context["addons"] == "Add-ons excluded from analysis."
