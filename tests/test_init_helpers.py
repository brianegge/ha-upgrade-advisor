"""Tests for __init__.py helper functions and error handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor import _component_anchor
from custom_components.upgrade_advisor.analyzer import AnalysisResult
from custom_components.upgrade_advisor.const import CONF_AGENT_ID, DOMAIN

from .conftest import MOCK_AGENT_ID

# --- _component_anchor ---


def test_component_anchor_simple() -> None:
    """Test anchor generation from simple name."""
    assert _component_anchor("Home Assistant") == "home-assistant"


def test_component_anchor_single_word() -> None:
    """Test anchor generation from single word."""
    assert _component_anchor("Powercalc") == "powercalc"


def test_component_anchor_multi_word() -> None:
    """Test anchor generation from multi-word name."""
    assert _component_anchor("Dahua Update") == "dahua-update"


# --- _store_result with anchors ---


async def test_store_result_includes_anchors(hass: HomeAssistant) -> None:
    """Test that stored report includes HTML anchors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    result = AnalysisResult(
        report="# Test Report\nSome content",
        risk_level="low",
        breaking_change_count=0,
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2024.1.0",
        target_version="2024.2.0",
    )
    coordinator._store_result(result)

    assert '<a id="home-assistant"></a>' in coordinator.report


async def test_store_result_error_includes_anchor(hass: HomeAssistant) -> None:
    """Test that error reports also include anchors."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    result = AnalysisResult(
        report="",
        error="Error talking to API",
        upgrade_type="HACS Component",
        component_name="Powercalc Update",
        current_version="1.0",
        target_version="2.0",
    )
    coordinator._store_result(result)

    assert '<a id="powercalc-update"></a>' in coordinator.report
    assert "Error talking to API" in coordinator.report


# --- _async_output_error ---


async def test_async_output_error_creates_notification(hass: HomeAssistant) -> None:
    """Test that analysis errors create persistent notifications."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    result = AnalysisResult(
        report="",
        error="Error talking to API",
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2024.1.0",
        target_version="2024.2.0",
    )

    with patch("custom_components.upgrade_advisor.async_create_notification") as mock_notify:
        await coordinator._async_output_error(result)

    mock_notify.assert_called_once()
    args = mock_notify.call_args
    assert "Error talking to API" in args[0][1]
    assert "analysis failed" in args.kwargs.get("title", args[0][2] if len(args[0]) > 2 else "")


# --- _async_output_results with anchors ---


async def test_async_output_results_dashboard_link_has_anchor(hass: HomeAssistant) -> None:
    """Test that dashboard link includes component anchor."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        options={"dashboard_path": "upgrade-advisor"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Set up event entity reference
    hass.data[DOMAIN][entry.entry_id]["event_entity"] = None

    result = AnalysisResult(
        report="# Report\nContent",
        risk_level="low",
        breaking_change_count=0,
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2024.1.0",
        target_version="2024.2.0",
    )

    with patch("custom_components.upgrade_advisor.async_create_notification") as mock_notify:
        await coordinator._async_output_results(result)

    mock_notify.assert_called_once()
    message = mock_notify.call_args[0][1]
    assert "#home-assistant" in message


# --- startup delay scheduling ---


async def test_startup_delay_uses_async_call_later(hass: HomeAssistant) -> None:
    """Test that startup scan is scheduled with async_call_later."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.upgrade_advisor.async_call_later",
        return_value=MagicMock(),
    ) as mock_call_later:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_call_later.assert_called_once()
    # First arg is hass, second is delay seconds (300)
    assert mock_call_later.call_args[0][1] == 300


async def test_startup_no_scan_when_disabled(hass: HomeAssistant) -> None:
    """Test that startup scan is skipped when both pre- and post-upgrade are disabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        options={"scan_on_update_available": False, "post_upgrade_check": False},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.upgrade_advisor.async_call_later",
        return_value=MagicMock(),
    ) as mock_call_later:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_call_later.assert_not_called()


async def test_startup_scan_runs_for_post_upgrade_only(hass: HomeAssistant) -> None:
    """Startup scan should still be scheduled when only post-upgrade check is enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data={CONF_AGENT_ID: MOCK_AGENT_ID},
        options={"scan_on_update_available": False, "post_upgrade_check": True},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.upgrade_advisor.async_call_later",
        return_value=MagicMock(),
    ) as mock_call_later:
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_call_later.assert_called_once()
