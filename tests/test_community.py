"""Tests for the community signal (release age, adoption)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor import community
from custom_components.upgrade_advisor.community import (
    ANALYTICS_CORE_URL,
    ANALYTICS_CUSTOM_URL,
    Adoption,
    CommunitySignal,
    async_find_custom_integration_domain,
    async_get_core_adoption,
    async_get_core_release_dates,
    async_get_custom_adoption,
    async_get_release_date,
    compute_adoption,
    format_community_signal,
    insert_before_footer,
)


class _FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self, content_type: str | None = "application/json") -> Any:
        return self._payload


class FakeSession:
    """Minimal aiohttp stand-in: maps URL prefixes to (status, payload)."""

    def __init__(self, routes: dict[str, tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append(url)
        for prefix, (status, payload) in self.routes.items():
            if url.startswith(prefix):
                return _FakeResponse(status, payload)
        return _FakeResponse(404, {})


@pytest.fixture(autouse=True)
def _clear_cache():
    community._clear_cache()
    yield
    community._clear_cache()


def test_compute_adoption_counts_target_and_newer() -> None:
    counts = {"2026.11.1": 100, "2026.11.0": 50, "2026.10.4": 50, "2026.12.0b1": 999, "2026.11.2": 0}
    adoption = compute_adoption(counts, "2026.11.0")
    assert adoption == Adoption(target_installs=50, at_or_newer=150, total=200)
    assert adoption.pct == 75.0


def test_compute_adoption_ignores_prerelease_and_local_builds() -> None:
    counts = {"1.0.0": 10, "1.1.0-beta1": 5, "1.1.0+local1": 5, "1.1.0": 10}
    adoption = compute_adoption(counts, "1.1.0")
    assert adoption is not None
    assert adoption.total == 20
    assert adoption.pct == 50.0


def test_compute_adoption_handles_empty() -> None:
    assert compute_adoption({}, "1.0") is None
    assert compute_adoption({"1.0": 1}, "") is None
    assert Adoption(0, 0, 0).pct == 0.0


def test_release_age_days() -> None:
    now = datetime(2026, 11, 11, 12, tzinfo=UTC)
    signal = CommunitySignal(release_date=now - timedelta(days=6, hours=3))
    assert signal.release_age_days(now) == 6
    assert CommunitySignal().release_age_days(now) is None
    # Clock skew never produces a negative age
    assert CommunitySignal(release_date=now + timedelta(hours=1)).release_age_days(now) == 0


def test_format_community_signal() -> None:
    now = datetime(2026, 11, 11, tzinfo=UTC)
    signal = CommunitySignal(
        release_date=now - timedelta(days=6),
        adoption=Adoption(target_installs=1, at_or_newer=245, total=1000),
    )
    text = format_community_signal(signal, "2026.11.1", now)
    assert text == "**Community signal:** released 6 days ago · 24.5% of 1,000 reporting installs on 2026.11.1 or newer"
    assert "released today" in format_community_signal(CommunitySignal(release_date=now), "1", now)
    assert "1 day ago" in format_community_signal(CommunitySignal(release_date=now - timedelta(days=1)), "1", now)
    assert format_community_signal(CommunitySignal(), "1", now) == ""


def test_insert_before_footer() -> None:
    report = "## X\n\nVerdict\n\nRISK_LEVEL: Low\nBREAKING_CHANGES: 0"
    out = insert_before_footer(report, "BLOCK")
    assert out == "## X\n\nVerdict\n\nBLOCK\n\nRISK_LEVEL: Low\nBREAKING_CHANGES: 0"
    assert insert_before_footer("## X", "BLOCK") == "## X\n\nBLOCK"
    assert insert_before_footer("## X", "") == "## X"


async def test_core_adoption_fetch_is_cached() -> None:
    session = FakeSession({ANALYTICS_CORE_URL: (200, {"versions": {"2026.11.0": 3, "2026.10.0": 1}})})
    first = await async_get_core_adoption(session, "2026.11.0")
    second = await async_get_core_adoption(session, "2026.10.0")
    assert first is not None and first.pct == 75.0
    assert second is not None and second.pct == 100.0
    assert session.calls == [ANALYTICS_CORE_URL]


async def test_core_adoption_fetch_failure_returns_none() -> None:
    session = FakeSession({ANALYTICS_CORE_URL: (500, {})})
    assert await async_get_core_adoption(session, "2026.11.0") is None


async def test_custom_adoption_by_domain() -> None:
    session = FakeSession(
        {ANALYTICS_CUSTOM_URL: (200, {"alexa_media": {"total": 30, "versions": {"5.20.0": 10, "5.19.0": 20}}})}
    )
    adoption = await async_get_custom_adoption(session, "alexa_media", "5.20.0")
    assert adoption is not None and adoption.at_or_newer == 10
    assert await async_get_custom_adoption(session, "missing", "1.0") is None


async def test_release_date_tries_v_prefix() -> None:
    session = FakeSession(
        {
            "https://api.github.com/repos/o/r/releases/tags/v1.2.0": (200, {"published_at": "2026-11-05T10:00:00Z"}),
        }
    )
    date = await async_get_release_date(session, "o/r", "1.2.0")
    assert date == datetime(2026, 11, 5, 10, tzinfo=UTC)
    assert session.calls[0].endswith("/tags/1.2.0")


async def test_release_date_missing() -> None:
    assert await async_get_release_date(FakeSession({}), "o/r", "1.2.0") is None


async def test_core_release_dates_skip_prereleases() -> None:
    session = FakeSession(
        {
            "https://api.github.com/repos/home-assistant/core/releases": (
                200,
                [
                    {"tag_name": "2026.11.1", "published_at": "2026-11-07T00:00:00Z"},
                    {"tag_name": "2026.12.0b0", "prerelease": True, "published_at": "2026-11-26T00:00:00Z"},
                    {"tag_name": "2026.11.0", "published_at": None},
                ],
            )
        }
    )
    dates = await async_get_core_release_dates(session)
    assert list(dates) == ["2026.11.1"]


async def test_find_custom_integration_domain(hass: HomeAssistant) -> None:
    components = {
        "alexa_media": SimpleNamespace(
            manifest={"documentation": "https://github.com/alandtse/alexa_media_player/wiki"}
        ),
        "other": SimpleNamespace(manifest={"issue_tracker": "https://github.com/someone/other/issues"}),
        "prefix_trap": SimpleNamespace(manifest={"documentation": "https://github.com/someone/other-thing"}),
    }
    with patch("homeassistant.loader.async_get_custom_components", AsyncMock(return_value=components)):
        assert await async_find_custom_integration_domain(hass, "alandtse/alexa_media_player") == "alexa_media"
        assert await async_find_custom_integration_domain(hass, "Someone/Other") == "other"
        assert await async_find_custom_integration_domain(hass, "nobody/nothing") is None


async def test_coordinator_community_signal_core_and_hacs(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.upgrade_advisor import UpgradeAdvisorCoordinator
    from custom_components.upgrade_advisor.const import DOMAIN

    from .conftest import MOCK_CONFIG

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG.copy(), unique_id=DOMAIN)
    coordinator = UpgradeAdvisorCoordinator(hass, entry)
    released = datetime(2026, 11, 5, tzinfo=UTC)
    adoption = Adoption(1, 2, 4)
    prefix = "custom_components.upgrade_advisor"

    with (
        patch(f"{prefix}.async_get_core_release_dates", AsyncMock(return_value={"2026.11.1": released})),
        patch(f"{prefix}.async_get_core_adoption", AsyncMock(return_value=adoption)),
    ):
        core = await coordinator._async_get_community_signal(None, None, "2026.10.4", "2026.11.1")
    assert core.release_date == released and core.adoption == adoption

    custom_adoption = AsyncMock(return_value=adoption)
    with (
        patch(f"{prefix}.async_get_release_date", AsyncMock(return_value=released)),
        patch(f"{prefix}.async_find_custom_integration_domain", AsyncMock(return_value="alexa_media")),
        patch(f"{prefix}.async_get_custom_adoption", custom_adoption),
    ):
        hacs = await coordinator._async_get_community_signal(None, "o/r", "1.0", "1.1")
    assert hacs.adoption == adoption
    custom_adoption.assert_awaited_once_with(None, "alexa_media", "1.1")

    # A failure never breaks the analysis
    with patch(f"{prefix}.async_get_core_release_dates", AsyncMock(side_effect=RuntimeError)):
        failed = await coordinator._async_get_community_signal(None, None, "a", "b")
    assert failed == CommunitySignal()
