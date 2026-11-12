"""Community signal: release age and adoption across Home Assistant installs.

Adoption numbers come from Home Assistant's public, opt-in analytics
(https://analytics.home-assistant.io). The data is a daily snapshot, so a
release that is only hours old will show no adoption yet.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from .const import ADOPTION_GATE_MAX_AGE_DAYS, GITHUB_API_BASE, HA_CORE_REPO
from .github import _is_version_between, _version_tuple
from .sanitize import strip_markup

_LOGGER = logging.getLogger(__name__)

ANALYTICS_CORE_URL = "https://analytics.home-assistant.io/current_data.json"
ANALYTICS_CUSTOM_URL = "https://analytics.home-assistant.io/custom_integrations.json"

_TIMEOUT = ClientTimeout(total=30)
# Analytics is regenerated roughly daily; refetching more often wastes bandwidth.
_CACHE_TTL_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, Any]] = {}

GITHUB_SEARCH_URL = f"{GITHUB_API_BASE}/search/issues"
_INTEGRATION_LABEL = "integration: "
# Issues tagged with more integrations than this are sweeping chores
# (e.g. "add X to all integrations"), not regression reports.
_MAX_INTEGRATION_LABELS = 3
_MAX_ISSUE_PAGES = 5
_MAX_TITLE_CHARS = 120
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]<>|#~])")

# Version strings we don't count toward adoption (betas, dev builds, local forks).
_PRERELEASE_RE = re.compile(r"(a|b|rc|dev|beta|alpha|\+)", re.IGNORECASE)


@dataclass
class Adoption:
    """How many reporting installs run the target version or newer."""

    target_installs: int
    at_or_newer: int
    total: int

    @property
    def pct(self) -> float:
        """Share of reporting installs on the target version or newer, in percent."""
        if self.total <= 0:
            return 0.0
        return round(100.0 * self.at_or_newer / self.total, 1)


@dataclass
class CommunityIssue:
    """An open GitHub issue that matches an installed integration."""

    number: int
    title: str
    url: str
    reactions: int
    comments: int
    integrations: list[str]


@dataclass
class CommunitySignal:
    """Release age, adoption and open bug reports for one component's target version."""

    release_date: datetime | None = None
    adoption: Adoption | None = None
    issues: list[CommunityIssue] = field(default_factory=list)
    issues_since: str | None = None

    def release_age_days(self, now: datetime | None = None) -> int | None:
        """Whole days since the target version was released."""
        if self.release_date is None:
            return None
        now = now or datetime.now(tz=UTC)
        return max(0, (now - self.release_date).days)


def _clear_cache() -> None:
    """Drop cached analytics (tests)."""
    _cache.clear()


async def _async_get_json_cached(session: ClientSession, url: str) -> Any | None:
    """Fetch JSON with an in-memory TTL cache."""
    cached = _cache.get(url)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
    except Exception:
        _LOGGER.warning("Failed to fetch %s", url, exc_info=True)
        return None
    _cache[url] = (time.monotonic(), data)
    return data


def compute_adoption(version_counts: dict[str, int], target_version: str) -> Adoption | None:
    """Compute adoption of target_version from a {version: install_count} map."""
    if not version_counts or not target_version:
        return None
    target = _version_tuple(target_version)
    if not target:
        return None
    target_installs = 0
    at_or_newer = 0
    total = 0
    for version, count in version_counts.items():
        if not isinstance(count, int) or _PRERELEASE_RE.search(version.lstrip("v")):
            continue
        parsed = _version_tuple(version)
        if not parsed:
            continue
        total += count
        if parsed >= target:
            at_or_newer += count
        if parsed == target:
            target_installs += count
    if total == 0:
        return None
    return Adoption(target_installs=target_installs, at_or_newer=at_or_newer, total=total)


async def async_get_core_adoption(session: ClientSession, target_version: str) -> Adoption | None:
    """Adoption of a Home Assistant Core version."""
    data = await _async_get_json_cached(session, ANALYTICS_CORE_URL)
    if not isinstance(data, dict):
        return None
    return compute_adoption(data.get("versions") or {}, target_version)


async def async_get_custom_adoption(session: ClientSession, domain: str, target_version: str) -> Adoption | None:
    """Adoption of a custom (HACS) integration version, by integration domain."""
    data = await _async_get_json_cached(session, ANALYTICS_CUSTOM_URL)
    if not isinstance(data, dict):
        return None
    entry = data.get(domain)
    if not isinstance(entry, dict):
        return None
    return compute_adoption(entry.get("versions") or {}, target_version)


def _parse_github_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def async_get_release_date(session: ClientSession, repo: str, tag: str) -> datetime | None:
    """Publish date of a GitHub release, trying the tag with and without a 'v' prefix."""
    candidates = [tag] if tag.startswith("v") else [tag, f"v{tag}"]
    for candidate in candidates:
        url = f"{GITHUB_API_BASE}/repos/{repo}/releases/tags/{candidate}"
        try:
            async with session.get(url, timeout=_TIMEOUT) as resp:
                if resp.status == 404:
                    continue
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            _LOGGER.debug("Failed to fetch release date for %s %s", repo, candidate, exc_info=True)
            return None
        return _parse_github_date(data.get("published_at") or data.get("created_at"))
    return None


async def async_get_core_release_dates(session: ClientSession) -> dict[str, datetime]:
    """Publish dates of recent stable Home Assistant Core releases, keyed by tag."""
    url = f"{GITHUB_API_BASE}/repos/{HA_CORE_REPO}/releases?per_page=100"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            releases = await resp.json()
    except Exception:
        _LOGGER.debug("Failed to list HA core releases", exc_info=True)
        return {}
    dates: dict[str, datetime] = {}
    for release in releases if isinstance(releases, list) else []:
        if release.get("prerelease"):
            continue
        tag = release.get("tag_name") or ""
        date = _parse_github_date(release.get("published_at"))
        if tag and date:
            dates[tag] = date
    return dates


async def async_find_custom_integration_domain(hass: Any, repo: str) -> str | None:
    """Map a GitHub owner/repo to the loaded custom integration it ships.

    Custom integrations point their manifest `documentation` / `issue_tracker`
    at their repo, which is the only reliable link from a HACS update entity
    back to an integration domain.
    """
    from homeassistant.loader import async_get_custom_components

    try:
        components = await async_get_custom_components(hass)
    except Exception:
        _LOGGER.debug("Could not list custom components", exc_info=True)
        return None
    needle = f"https://github.com/{repo.lower()}"
    for domain, integration in components.items():
        manifest = getattr(integration, "manifest", None) or {}
        for key in ("documentation", "issue_tracker"):
            url = str(manifest.get(key) or "").lower().rstrip("/")
            if url == needle or url.startswith(f"{needle}/") or url.startswith(f"{needle}#"):
                return domain
    return None


def earliest_release_in_range(dates: dict[str, datetime], current: str, target: str) -> tuple[str, datetime] | None:
    """The first release after `current` up to `target` — when regressions could start appearing."""
    in_range = [(tag, date) for tag, date in dates.items() if _is_version_between(tag, current, target)]
    if not in_range:
        return (target, dates[target]) if target in dates else None
    return min(in_range, key=lambda item: _version_tuple(item[0]))


def _issue_from_item(item: dict[str, Any], domains: set[str]) -> CommunityIssue | None:
    if "pull_request" in item:
        return None
    labels = [label.get("name", "") for label in item.get("labels") or [] if isinstance(label, dict)]
    integrations = [name[len(_INTEGRATION_LABEL) :] for name in labels if name.startswith(_INTEGRATION_LABEL)]
    if not integrations or len(integrations) > _MAX_INTEGRATION_LABELS:
        return None
    matched = sorted(i for i in integrations if i in domains)
    if not matched:
        return None
    url = item.get("html_url") or ""
    if not url.startswith(f"https://github.com/{HA_CORE_REPO}/issues/"):
        return None
    return CommunityIssue(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        url=url,
        reactions=int((item.get("reactions") or {}).get("total_count") or 0),
        comments=int(item.get("comments") or 0),
        integrations=matched,
    )


async def async_get_core_issues(
    session: ClientSession, since: datetime, domains: set[str], limit: int = 5
) -> list[CommunityIssue]:
    """Open Home Assistant Core issues filed since `since` for installed integrations.

    Core labels every issue `integration: <domain>`, so one unauthenticated
    search (paged, most-reacted first) is filtered locally against the
    domains loaded in this installation. Ranked by reactions, then comments.
    """
    if not domains:
        return []
    query = f"repo:{HA_CORE_REPO} is:issue is:open created:>={since.date().isoformat()}"
    found: dict[int, CommunityIssue] = {}
    for page in range(1, _MAX_ISSUE_PAGES + 1):
        params = {"q": query, "sort": "reactions", "order": "desc", "per_page": "100", "page": str(page)}
        try:
            async with session.get(GITHUB_SEARCH_URL, params=params, timeout=_TIMEOUT) as resp:
                if resp.status in (403, 422, 429):
                    _LOGGER.debug("GitHub issue search stopped with HTTP %d", resp.status)
                    break
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            _LOGGER.warning("Failed to search GitHub issues", exc_info=True)
            break
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            break
        for item in items:
            if isinstance(item, dict) and (issue := _issue_from_item(item, domains)):
                found[issue.number] = issue
        if len(items) < 100:
            break
    ranked = sorted(found.values(), key=lambda i: (i.reactions, i.comments, i.number), reverse=True)
    return ranked[:limit]


def _safe_title(title: str) -> str:
    """Issue titles are user-submitted: flatten markup and escape markdown."""
    flat = " ".join(strip_markup(title).split())
    if len(flat) > _MAX_TITLE_CHARS:
        flat = flat[: _MAX_TITLE_CHARS - 1].rstrip() + "…"
    return _MARKDOWN_SPECIAL.sub(r"\\\1", flat)


def format_issues_for_prompt(signal: CommunitySignal) -> str:
    """One line per issue for the AI prompt (empty when there are none)."""
    lines = []
    for issue in signal.issues:
        lines.append(
            f"- #{issue.number} [{', '.join(issue.integrations)}] {_safe_title(issue.title)} "
            f"({issue.reactions} reactions, {issue.comments} comments)"
        )
    return "\n".join(lines)


def deferral_reason(
    signal: CommunitySignal,
    min_age_days: int,
    min_adoption_pct: float,
    now: datetime | None = None,
) -> str | None:
    """Why an automatic analysis should wait, or None if it can run now.

    Fails open: an unknown release date or missing adoption data never
    blocks. The adoption threshold is waived once the release is
    ADOPTION_GATE_MAX_AGE_DAYS old, so low-install integrations still get
    analyzed eventually.
    """
    reasons: list[str] = []
    age = signal.release_age_days(now)
    if min_age_days > 0 and age is not None and age < min_age_days:
        reasons.append(f"released {age} of {min_age_days} days ago")
    adoption = signal.adoption
    if (
        min_adoption_pct > 0
        and adoption is not None
        and adoption.pct < min_adoption_pct
        and (age is None or age < ADOPTION_GATE_MAX_AGE_DAYS)
    ):
        reasons.append(f"adoption {adoption.pct:g}% below {min_adoption_pct:g}%")
    return "; ".join(reasons) or None


def format_community_signal(signal: CommunitySignal, target_version: str, now: datetime | None = None) -> str:
    """Render the community signal as a short markdown block (empty if no data)."""
    parts: list[str] = []
    age = signal.release_age_days(now)
    if age is not None:
        parts.append("released today" if age == 0 else f"released {age} day{'s' if age != 1 else ''} ago")
    if signal.adoption is not None:
        a = signal.adoption
        parts.append(f"{a.pct:g}% of {a.total:,} reporting installs on {target_version} or newer")
    blocks: list[str] = []
    if parts:
        blocks.append("**Community signal:** " + " · ".join(parts))
    if signal.issues:
        since = f" since {signal.issues_since}" if signal.issues_since else ""
        lines = [f"**Open bug reports{since} for your integrations:**"]
        for issue in signal.issues:
            stats = f"{issue.reactions} reaction{'s' if issue.reactions != 1 else ''}"
            stats += f", {issue.comments} comment{'s' if issue.comments != 1 else ''}"
            lines.append(
                f"- [#{issue.number}]({issue.url}) {_safe_title(issue.title)} — "
                f"`{', '.join(issue.integrations)}` · {stats}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def insert_before_footer(report: str, block: str) -> str:
    """Insert a block above the RISK_LEVEL/BREAKING_CHANGES footer (or append)."""
    if not block:
        return report
    match = re.search(r"^RISK_LEVEL:", report, re.MULTILINE)
    if match is None:
        return f"{report.rstrip()}\n\n{block}"
    head = report[: match.start()].rstrip()
    return f"{head}\n\n{block}\n\n{report[match.start() :]}"
