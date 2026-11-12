"""Tests for the community signal (release age, adoption)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor import community, github
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
    github._clear_release_cache()
    yield
    community._clear_cache()
    github._clear_release_cache()


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


def test_compute_adoption_pads_short_versions() -> None:
    # Analytics reporting "2.0" counts toward a "2.0.0" target
    adoption = compute_adoption({"2.0": 3, "1.9": 1}, "2.0.0")
    assert adoption is not None and adoption.at_or_newer == 3


def test_compute_adoption_counts_prerelease_target() -> None:
    adoption = compute_adoption({"2.0.0b1": 2, "2.0.0b0": 5, "1.9.0": 2}, "2.0.0b1")
    assert adoption is not None
    assert adoption.target_installs == 2 and adoption.total == 4


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
    bold = "## X\n\n**RISK_LEVEL:** Medium\n**BREAKING_CHANGES:** 1"
    assert insert_before_footer(bold, "BLOCK") == "## X\n\nBLOCK\n\n**RISK_LEVEL:** Medium\n**BREAKING_CHANGES:** 1"
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
    # Failures are not cached
    session.routes[ANALYTICS_CORE_URL] = (200, {"versions": {"2026.11.0": 1}})
    assert await async_get_core_adoption(session, "2026.11.0") is not None


async def test_analytics_cache_keeps_only_version_maps_and_expires() -> None:
    session = FakeSession(
        {ANALYTICS_CUSTOM_URL: (200, {"a": {"total": 1, "versions": {"1.0": 1}}, "junk": 5, "b": {"total": 0}})}
    )
    await async_get_custom_adoption(session, "a", "1.0")
    assert community._cache[ANALYTICS_CUSTOM_URL][1] == {"a": {"1.0": 1}}
    with patch.object(community.time, "monotonic", return_value=community.time.monotonic() + 7200):
        await async_get_custom_adoption(session, "a", "1.0")
    assert len(session.calls) == 2


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


async def test_release_date_shares_cache_with_release_notes() -> None:
    session = FakeSession(
        {
            "https://api.github.com/repos/o/r/releases/tags/v1.2.0": (
                200,
                {"published_at": "2026-11-05T10:00:00Z", "body": "notes"},
            ),
        }
    )
    assert await github.async_get_hacs_release_notes(session, "o/r", "1.2.0") == "notes"
    calls = len(session.calls)
    assert await async_get_release_date(session, "o/r", "1.2.0") is not None
    assert len(session.calls) == calls


async def test_core_release_list_shared_and_beta_tags_skipped() -> None:
    session = FakeSession(
        {
            "https://api.github.com/repos/home-assistant/core/releases": (
                200,
                [
                    {"tag_name": "2026.11.1", "published_at": "2026-11-07T00:00:00Z", "body": "fix"},
                    {"tag_name": "2026.12.0b0", "published_at": "2026-11-26T00:00:00Z"},
                ],
            )
        }
    )
    assert "fix" in await github.async_get_ha_release_notes_range(session, "2026.11.0", "2026.11.1")
    assert list(await async_get_core_release_dates(session)) == ["2026.11.1"]
    assert len(session.calls) == 1


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


def _issue_item(number: int, labels: list[str], reactions: int = 0, comments: int = 0, **extra: Any) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/home-assistant/core/issues/{number}",
        "labels": [{"name": name} for name in labels],
        "reactions": {"total_count": reactions},
        "comments": comments,
        **extra,
    }


SEARCH_PREFIX = "https://api.github.com/search/issues"


async def test_core_issues_filters_to_installed_integrations() -> None:
    items = [
        _issue_item(1, ["integration: zha"], reactions=1, comments=9),
        _issue_item(2, ["integration: hue", "bug"], reactions=5),
        _issue_item(3, ["integration: tuya"], reactions=50),  # not installed
        _issue_item(4, ["bug"]),  # no integration label
        _issue_item(5, [f"integration: d{i}" for i in range(4)] + ["integration: zha"]),  # sweeping chore
        _issue_item(6, ["integration: zha"], pull_request={}),  # a PR
        _issue_item(7, ["integration: zha"], html_url="https://evil.example/7"),
    ]
    session = FakeSession({SEARCH_PREFIX: (200, {"items": items})})
    issues = await community.async_get_core_issues(
        session, datetime(2026, 11, 5, tzinfo=UTC), {"zha", "hue", "d0", "d1", "d2", "d3"}
    )
    assert [i.number for i in issues] == [2, 1]
    assert issues[0].integrations == ["hue"]
    assert len(session.calls) == 1


async def test_core_issues_pages_and_limits() -> None:
    page = [_issue_item(n, ["integration: zha"], reactions=n) for n in range(100)]
    session = FakeSession({SEARCH_PREFIX: (200, {"items": page})})
    issues = await community.async_get_core_issues(session, datetime(2026, 11, 5, tzinfo=UTC), {"zha"}, limit=3)
    assert [i.number for i in issues] == [99, 98, 97]
    # Full pages keep paging, up to the cap
    assert len(session.calls) == 5


async def test_core_issues_rate_limited_or_no_domains() -> None:
    since = datetime(2026, 11, 5, tzinfo=UTC)
    assert await community.async_get_core_issues(FakeSession({SEARCH_PREFIX: (403, {})}), since, {"zha"}) == []
    session = FakeSession({SEARCH_PREFIX: (200, {"items": []})})
    assert await community.async_get_core_issues(session, since, set()) == []
    assert session.calls == []


def test_earliest_release_in_range() -> None:
    d = {
        "2026.10.4": datetime(2026, 10, 20, tzinfo=UTC),
        "2026.11.0": datetime(2026, 11, 5, tzinfo=UTC),
        "2026.11.1": datetime(2026, 11, 7, tzinfo=UTC),
    }
    assert community.earliest_release_in_range(d, "2026.10.4", "2026.11.1") == ("2026.11.0", d["2026.11.0"])
    assert community.earliest_release_in_range(d, "2026.11.0", "2026.11.1") == ("2026.11.1", d["2026.11.1"])
    assert community.earliest_release_in_range({}, "1", "2") is None
    # Unknown installed version: only the target release, never a months-old backlog
    assert community.earliest_release_in_range(d, "", "2026.11.1") == ("2026.11.1", d["2026.11.1"])


def test_issue_titles_are_escaped() -> None:
    issue = community.CommunityIssue(
        number=9,
        title="Broken ![x](https://evil.example/i.png) <b>bold</b> [click](https://evil.example) *now*",
        url="https://github.com/home-assistant/core/issues/9",
        reactions=1,
        comments=2,
        integrations=["zha"],
    )
    signal = CommunitySignal(issues=[issue], issues_since="2026.11.0")
    block = format_community_signal(signal, "2026.11.1")
    assert "evil.example" not in block
    assert "<b>" not in block
    assert "\\*now\\*" in block
    assert block.startswith("**Open bug reports since 2026.11.0 for your integrations:**\n- [#9](")
    assert "1 reaction, 2 comments" in block
    assert community.format_issues_for_prompt(signal).startswith("- #9 [zha] Broken")
    assert community.format_issues_for_prompt(CommunitySignal()) == ""


async def test_coordinator_fetches_issues_since_first_release_in_range(hass: HomeAssistant) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.upgrade_advisor import UpgradeAdvisorCoordinator
    from custom_components.upgrade_advisor.const import CONF_INCLUDE_ISSUES, DOMAIN

    from .conftest import MOCK_CONFIG

    hass.config.components.update({"zha", "zha.light"})
    dates = {"2026.11.0": datetime(2026, 11, 5, tzinfo=UTC), "2026.11.1": datetime(2026, 11, 7, tzinfo=UTC)}
    prefix = "custom_components.upgrade_advisor"
    get_issues = AsyncMock(return_value=[])

    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG.copy(), unique_id=DOMAIN)
    coordinator = UpgradeAdvisorCoordinator(hass, entry)
    with (
        patch(f"{prefix}.async_get_core_release_dates", AsyncMock(return_value=dates)),
        patch(f"{prefix}.async_get_core_adoption", AsyncMock(return_value=None)),
        patch(f"{prefix}.async_get_core_issues", get_issues),
    ):
        signal = await coordinator._async_get_community_signal(None, None, "2026.10.4", "2026.11.1")
        assert signal.issues_since == "2026.11.0"
        _, since, domains = get_issues.await_args.args
        assert since == dates["2026.11.0"]
        assert "zha" in domains and "zha.light" not in domains

        get_issues.reset_mock()
        disabled = MockConfigEntry(
            domain=DOMAIN, data=MOCK_CONFIG.copy(), options={CONF_INCLUDE_ISSUES: False}, unique_id="x"
        )
        await UpgradeAdvisorCoordinator(hass, disabled)._async_get_community_signal(
            None, None, "2026.10.4", "2026.11.1"
        )
        get_issues.assert_not_awaited()
