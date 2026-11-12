"""Tests for Upgrade Advisor sensor entities."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor.const import DOMAIN

from .conftest import MOCK_CONFIG


def _get_entity_id(hass: HomeAssistant, entry: MockConfigEntry, unique_id_suffix: str) -> str | None:
    """Look up entity ID by unique_id suffix."""
    ent_reg = er.async_get(hass)
    unique_id = f"{entry.entry_id}_{unique_id_suffix}"
    entity_entry = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
    return entity_entry


async def test_status_sensor_initial_state(hass: HomeAssistant) -> None:
    """Test status sensor starts as idle."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _get_entity_id(hass, entry, "status")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "idle"


async def test_risk_sensor_initial_state(hass: HomeAssistant) -> None:
    """Test risk sensor starts as unknown."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _get_entity_id(hass, entry, "risk")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unknown"


async def test_status_sensor_attributes(hass: HomeAssistant) -> None:
    """Test status sensor has expected attributes."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    entity_id = _get_entity_id(hass, entry, "status")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    attrs = state.attributes
    assert "current_version" in attrs
    assert "available_version" in attrs
    assert "last_analysis" in attrs
    assert "breaking_change_count" in attrs


async def test_status_sensor_updates_on_analysis(hass: HomeAssistant) -> None:
    """Test status sensor updates when coordinator state changes."""
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
    coordinator.status = "report_ready"
    coordinator.risk_level = "low"
    coordinator.report = "Test report"
    coordinator.breaking_change_count = 0
    coordinator._async_notify_listeners()
    await hass.async_block_till_done()

    entity_id = _get_entity_id(hass, entry, "status")
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "report_ready"
    assert state.attributes["report"] == "Test report"


async def test_status_sensor_exposes_community_attributes(hass: HomeAssistant) -> None:
    """release_age_days / adoption_pct appear only when known."""
    from unittest.mock import MagicMock

    from custom_components.upgrade_advisor.sensor import UpgradeAdvisorStatusSensor

    coordinator = MagicMock()
    coordinator.report = None
    coordinator.post_upgrade_report = None
    coordinator.release_age_days = 6
    coordinator.adoption_pct = 24.5
    entry = MagicMock()
    entry.entry_id = "abc"
    attrs = UpgradeAdvisorStatusSensor(coordinator, entry).extra_state_attributes
    assert attrs["release_age_days"] == 6
    assert attrs["adoption_pct"] == 24.5

    coordinator.release_age_days = None
    coordinator.adoption_pct = None
    attrs = UpgradeAdvisorStatusSensor(coordinator, entry).extra_state_attributes
    assert "release_age_days" not in attrs and "adoption_pct" not in attrs
