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
        assert coordinator.deferred["Home Assistant"] == {
            "entity_id": HA_CORE_UPDATE_ENTITY,
            "target_version": "2",
            "reason": "released 1 of 3 days ago",
        }

        # A manual run ignores the gate and clears the deferral
        await coordinator._run_analysis("Home Assistant Core", "Home Assistant", "1", "2", None, HA_CORE_UPDATE_ENTITY)
        pipeline.assert_awaited_once()
        assert coordinator.deferred == {}


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
        "Home Assistant": {"entity_id": HA_CORE_UPDATE_ENTITY, "target_version": "2", "reason": "r"},
        "X": {"entity_id": "update.x", "target_version": "2", "reason": "r"},
        "Gone": {"entity_id": "update.gone", "target_version": "2", "reason": "r"},
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
    assert "Gone" not in coordinator.deferred


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
    coordinator.deferred = {"Home Assistant": {"target_version": "2026.11.1", "reason": "released 1 of 3 days ago"}}
    entry = MagicMock()
    entry.entry_id = "abc"
    attrs = UpgradeAdvisorStatusSensor(coordinator, entry).extra_state_attributes
    assert attrs["deferred_updates"] == {"Home Assistant": "2026.11.1: released 1 of 3 days ago"}
