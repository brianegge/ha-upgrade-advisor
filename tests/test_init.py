"""Tests for Upgrade Advisor setup and coordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor.const import DOMAIN
from custom_components.upgrade_advisor.pending_store import PendingAnalysis

from .conftest import MOCK_CONFIG


async def test_setup_entry(hass: HomeAssistant) -> None:
    """Test successful setup of a config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]
    assert "coordinator" in hass.data[DOMAIN][entry.entry_id]


async def test_unload_entry(hass: HomeAssistant) -> None:
    """Test unloading a config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_post_upgrade_runs_when_installed_matches_target(hass: HomeAssistant, mock_converse) -> None:
    """Pending entries whose target matches installed_version trigger a post-upgrade run."""
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

    hass.states.async_set(
        "update.home_assistant_core_update",
        "off",
        {"installed_version": "2026.4.3", "latest_version": "2026.4.3"},
    )

    pending = PendingAnalysis(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        entity_id="update.home_assistant_core_update",
        from_version="2026.4.2",
        target_version="2026.4.3",
        created_at=datetime.now(tz=UTC).isoformat(),
        check_tasks=[
            {"check": "backup_recent", "title": "Backup check", "severity": "warning"},
        ],
        pre_results=[
            {
                "check_id": "backup_recent",
                "title": "Backup check",
                "passed": True,
                "detail": "Last backup: 2026-04-17",
                "severity": "warning",
            }
        ],
    )
    coordinator.pending_store._entries = [pending]
    coordinator.pending_store._loaded = True

    post_converse_response = type(mock_converse.return_value)()
    post_converse_response.response.response_type.value = "action_done"
    post_converse_response.response.speech = {
        "plain": {"speech": ("## Post-upgrade report\nAll clear.\n\nPOST_STATUS: clean\nREGRESSIONS: 0")}
    }
    mock_converse.return_value = post_converse_response

    with patch.object(coordinator.pending_store, "async_save", new=AsyncMock()):
        await coordinator.async_run_post_upgrade_checks()

    assert coordinator.post_upgrade_status == "clean"
    assert coordinator.post_upgrade_regressions == 0
    assert coordinator.post_upgrade_report is not None
    assert "Post-upgrade report" in coordinator.post_upgrade_report
    # Pending entry cleared after run
    assert coordinator.pending_store._entries == []


async def test_post_upgrade_skips_when_installed_doesnt_match(hass: HomeAssistant) -> None:
    """Pending entry is left in place when installed version doesn't match the target."""
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
    hass.states.async_set(
        "update.home_assistant_core_update",
        "on",
        {"installed_version": "2026.4.2", "latest_version": "2026.4.3"},
    )

    pending = PendingAnalysis(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        entity_id="update.home_assistant_core_update",
        from_version="2026.4.2",
        target_version="2026.4.3",
        created_at=datetime.now(tz=UTC).isoformat(),
        check_tasks=[{"check": "backup_recent", "title": "Backup check"}],
        pre_results=[],
    )
    coordinator.pending_store._entries = [pending]
    coordinator.pending_store._loaded = True

    with patch.object(coordinator.pending_store, "async_save", new=AsyncMock()):
        await coordinator.async_run_post_upgrade_checks()

    # Entry still present — upgrade hasn't happened yet
    assert len(coordinator.pending_store._entries) == 1
    assert coordinator.post_upgrade_report is None


async def test_coordinator_initial_state(hass: HomeAssistant) -> None:
    """Test coordinator starts with idle state."""
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
    assert coordinator.status == "idle"
    assert coordinator.risk_level == "unknown"
    assert coordinator.report is None
    assert coordinator.breaking_change_count == 0
