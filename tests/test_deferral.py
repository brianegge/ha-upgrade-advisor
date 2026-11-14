"""Tests for the release-age / adoption gate on automatic analyses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor import UpgradeAdvisorCoordinator
from custom_components.upgrade_advisor.community import Adoption, CommunitySignal, deferral_reason
from custom_components.upgrade_advisor.const import (
    CONF_MIN_ADOPTION_PCT,
    CONF_MIN_RELEASE_AGE_DAYS,
    DOMAIN,
    HA_CORE_UPDATE_ENTITY,
)
from custom_components.upgrade_advisor.sensor import UpgradeAdvisorStatusSensor

from .conftest import MOCK_CONFIG

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _signal(age_days: int | None, pct_of_100: int | None) -> CommunitySignal:
    return CommunitySignal(
        release_date=None if age_days is None else NOW - timedelta(days=age_days),
        adoption=None if pct_of_100 is None else Adoption(0, pct_of_100, 100),
    )


def test_deferral_reason_age() -> None:
    assert deferral_reason(_signal(1, None), 3, 0, NOW) == "released 1 of 3 days ago"
    assert deferral_reason(_signal(3, None), 3, 0, NOW) is None


def test_deferral_reason_adoption() -> None:
    assert deferral_reason(_signal(5, 4), 0, 10, NOW) == "adoption 4% below 10%"
    assert deferral_reason(_signal(5, 10), 0, 10, NOW) is None
    # Waived once the release is old enough, so niche integrations aren't stuck
    assert deferral_reason(_signal(30, 4), 0, 10, NOW) is None


def test_deferral_reason_both_and_fail_open() -> None:
    assert deferral_reason(_signal(1, 4), 3, 10, NOW) == "released 1 of 3 days ago; adoption 4% below 10%"
    assert deferral_reason(_signal(None, None), 3, 10, NOW) is None
    assert deferral_reason(_signal(0, 0), 0, 0, NOW) is None
    # Unknown age: the 30-day waiver could never apply, so adoption can't block
    assert deferral_reason(_signal(None, 4), 0, 10, NOW) is None


def _coordinator(hass: HomeAssistant, **options: float) -> UpgradeAdvisorCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG.copy(), options=options, unique_id=DOMAIN)
    return UpgradeAdvisorCoordinator(hass, entry)


async def test_automatic_run_deferred_until_release_is_old_enough(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, **{CONF_MIN_RELEASE_AGE_DAYS: 3})
    young = CommunitySignal(release_date=datetime.now(tz=UTC) - timedelta(days=1))
    with (
        patch.object(coordinator, "_async_get_community_signal", AsyncMock(return_value=young)) as signal,
        patch.object(coordinator, "_run_analysis_pipeline", AsyncMock()) as pipeline,
    ):
        await coordinator._run_analysis(
            "Home Assistant Core", "Home Assistant", "1", "2", None, HA_CORE_UPDATE_ENTITY, True
        )
        pipeline.assert_not_awaited()
        assert signal.await_args.kwargs == {"include_issues": False}
        assert coordinator.deferred[HA_CORE_UPDATE_ENTITY] == {
            "component_name": "Home Assistant",
            "entity_id": HA_CORE_UPDATE_ENTITY,
            "target_version": "2",
            "reason": "released 1 of 3 days ago",
        }

        # A manual run ignores the gate and clears the deferral
        await coordinator._run_analysis("Home Assistant Core", "Home Assistant", "1", "2", None, HA_CORE_UPDATE_ENTITY)
        pipeline.assert_awaited_once()
        assert coordinator.deferred == {}


async def test_analyze_version_keeps_unrelated_deferral(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    deferral = {
        "component_name": "Home Assistant",
        "entity_id": HA_CORE_UPDATE_ENTITY,
        "target_version": "2026.11.1",
        "reason": "r",
    }
    coordinator.deferred = {HA_CORE_UPDATE_ENTITY: dict(deferral)}
    with patch.object(coordinator, "_run_analysis_pipeline", AsyncMock()):
        await coordinator.async_analyze_version("2026.10.4")
        assert HA_CORE_UPDATE_ENTITY in coordinator.deferred
        # Analyzing the held-back release itself satisfies the deferral
        await coordinator.async_analyze_version("2026.11.1")
        assert coordinator.deferred == {}


async def test_component_deferral_prunes_older_report(hass: HomeAssistant) -> None:
    from custom_components.upgrade_advisor.analyzer import AnalysisResult

    coordinator = _coordinator(hass, **{CONF_MIN_RELEASE_AGE_DAYS: 3})
    coordinator._store_result(
        AnalysisResult(report="## core v2", risk_level="high", component_name="Home Assistant", target_version="2")
    )
    young = CommunitySignal(release_date=datetime.now(tz=UTC))
    with patch.object(coordinator, "_async_get_community_signal", AsyncMock(return_value=young)):
        hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"installed_version": "1", "latest_version": "2"})
        await coordinator.async_analyze_core_update(automatic=True)
        assert "Home Assistant" in coordinator.reports  # same target: kept

        hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"installed_version": "1", "latest_version": "3"})
        await coordinator.async_analyze_core_update(automatic=True)
    assert coordinator.reports == {}
    assert coordinator.report is None
    assert coordinator.risk_level == "unknown"


async def test_no_gate_configured_skips_signal_lookup(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    with (
        patch.object(coordinator, "_async_get_community_signal", AsyncMock()) as signal,
        patch.object(coordinator, "_run_analysis_pipeline", AsyncMock()) as pipeline,
    ):
        await coordinator._run_analysis("HACS Component", "X", "1", "2", "o/r", "update.x", True)
    signal.assert_not_awaited()
    pipeline.assert_awaited_once()


async def test_recheck_deferred(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass, **{CONF_MIN_ADOPTION_PCT: 10})
    coordinator.deferred = {
        HA_CORE_UPDATE_ENTITY: {"entity_id": HA_CORE_UPDATE_ENTITY, "target_version": "2", "reason": "r"},
        "update.x": {"entity_id": "update.x", "target_version": "2", "reason": "r"},
        "update.gone": {"entity_id": "update.gone", "target_version": "2", "reason": "r"},
    }
    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on")
    hass.states.async_set("update.x", "on")
    hass.states.async_set("update.gone", "off")
    with (
        patch.object(coordinator, "async_analyze_core_update", AsyncMock()) as core,
        patch.object(coordinator, "async_analyze_hacs_update", AsyncMock()) as hacs,
    ):
        await coordinator.async_recheck_deferred()
    core.assert_awaited_once_with(automatic=True)
    hacs.assert_awaited_once_with("update.x", automatic=True)
    assert "update.gone" not in coordinator.deferred


async def test_recheck_drops_deferrals_when_scanning_disabled(hass: HomeAssistant) -> None:
    from custom_components.upgrade_advisor.const import CONF_SCAN_HACS, CONF_SCAN_ON_UPDATE

    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on")
    hass.states.async_set("update.x", "on")
    deferred = {
        HA_CORE_UPDATE_ENTITY: {"entity_id": HA_CORE_UPDATE_ENTITY, "target_version": "2", "reason": "r"},
        "update.x": {"entity_id": "update.x", "target_version": "2", "reason": "r"},
    }

    no_hacs = _coordinator(hass, **{CONF_SCAN_HACS: False})
    no_hacs.deferred = dict(deferred)
    with (
        patch.object(no_hacs, "async_analyze_core_update", AsyncMock()) as core,
        patch.object(no_hacs, "async_analyze_hacs_update", AsyncMock()) as hacs,
    ):
        await no_hacs.async_recheck_deferred()
    core.assert_awaited_once()
    hacs.assert_not_awaited()
    assert "update.x" not in no_hacs.deferred

    off = _coordinator(hass, **{CONF_SCAN_ON_UPDATE: False})
    off.deferred = dict(deferred)
    with patch.object(off, "async_analyze_core_update", AsyncMock()) as core:
        await off.async_recheck_deferred()
    core.assert_not_awaited()
    assert off.deferred == {}


async def test_deferred_scan_keeps_matching_report_and_prunes_stale(hass: HomeAssistant) -> None:
    from custom_components.upgrade_advisor.analyzer import AnalysisResult

    coordinator = _coordinator(hass, **{CONF_MIN_RELEASE_AGE_DAYS: 3})
    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"installed_version": "1", "latest_version": "2"})
    coordinator._store_result(
        AnalysisResult(report="## core", risk_level="high", component_name="Home Assistant", target_version="2")
    )
    coordinator._store_result(
        AnalysisResult(report="## card", risk_level="low", component_name="Old Card", target_version="5")
    )
    young = CommunitySignal(release_date=datetime.now(tz=UTC))
    with patch.object(coordinator, "_async_get_community_signal", AsyncMock(return_value=young)):
        await coordinator.async_analyze_available_update(automatic=True)

    # Core was held back but its report for the same target survives;
    # the card is no longer pending, so its report is gone.
    assert list(coordinator.reports) == ["Home Assistant"]
    assert coordinator.risk_level == "high"
    assert "## card" not in coordinator.report

    # A newer core target makes the old report stale too
    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"installed_version": "1", "latest_version": "3"})
    with patch.object(coordinator, "_async_get_community_signal", AsyncMock(return_value=young)):
        await coordinator.async_analyze_available_update(automatic=True)
    assert coordinator.reports == {}
    assert coordinator.report is None
    assert coordinator.risk_level == "unknown"


async def test_listeners_pass_automatic(hass: HomeAssistant) -> None:
    coordinator = _coordinator(hass)
    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"installed_version": "1", "latest_version": "2"})
    with patch.object(coordinator, "_run_analysis", AsyncMock()) as run:
        await coordinator.async_analyze_available_update(automatic=True)
        assert run.await_args.kwargs["automatic"] is True
        await coordinator.async_analyze_core_update()
        assert run.await_args.kwargs["automatic"] is False


def test_sensor_exposes_deferred_updates() -> None:
    coordinator = MagicMock()
    coordinator.report = None
    coordinator.post_upgrade_report = None
    coordinator.release_age_days = None
    coordinator.adoption_pct = None
    coordinator.deferred = {
        HA_CORE_UPDATE_ENTITY: {
            "component_name": "Home Assistant",
            "target_version": "2026.11.1",
            "reason": "released 1 of 3 days ago",
        }
    }
    entry = MagicMock()
    entry.entry_id = "abc"
    attrs = UpgradeAdvisorStatusSensor(coordinator, entry).extra_state_attributes
    assert attrs["deferred_updates"] == {
        HA_CORE_UPDATE_ENTITY: {
            "name": "Home Assistant",
            "target_version": "2026.11.1",
            "reason": "released 1 of 3 days ago",
        }
    }
