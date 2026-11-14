"""GitHub API client for fetching release notes."""

from __future__ import annotations

import logging
import re
import time
from html.parser import HTMLParser
from typing import Any

from aiohttp import ClientSession, ClientTimeout

from .const import GITHUB_API_BASE, HA_CORE_REPO

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = ClientTimeout(total=30)
_BLOG_TIMEOUT = ClientTimeout(total=60)

# Release metadata is read several times per analysis (release notes, release
# date, the deferral gate). A short cache keeps that to one request each, which
# matters under GitHub's 60 requests/hour unauthenticated limit.
_RELEASE_CACHE_TTL_SECONDS = 15 * 60
_MISSING = object()
_release_cache: dict[str, tuple[float, Any]] = {}


def _clear_release_cache() -> None:
    """Drop cached GitHub release metadata (tests)."""
    _release_cache.clear()


def _cache_get(url: str) -> Any:
    now = time.monotonic()
    for key in [k for k, (stamp, _) in _release_cache.items() if now - stamp >= _RELEASE_CACHE_TTL_SECONDS]:
        del _release_cache[key]
    cached = _release_cache.get(url)
    return _MISSING if cached is None else cached[1]


async def async_get_release(session: ClientSession, repo: str, tag: str) -> dict | None:
    """Release payload for one tag (None if missing or on error). Cached briefly."""
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    cached = _cache_get(url)
    if cached is not _MISSING:
        return cached
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 404:
                _LOGGER.debug("No release found for %s tag %s", repo, tag)
                _release_cache[url] = (time.monotonic(), None)
                return None
            resp.raise_for_status()
            data = await resp.json()
    except Exception:
        _LOGGER.exception("Failed to fetch release %s tag %s", repo, tag)
        return None
    release = data if isinstance(data, dict) else None
    _release_cache[url] = (time.monotonic(), release)
    return release


async def async_get_release_with_v_fallback(session: ClientSession, repo: str, tag: str) -> dict | None:
    """Release payload for a tag, retrying with a 'v' prefix (common for HACS repos)."""
    release = await async_get_release(session, repo, tag)
    if release is None and not tag.startswith("v"):
        release = await async_get_release(session, repo, f"v{tag}")
    return release


def is_stable_core_tag(release: dict) -> bool:
    """True for a stable (non-beta, non-dev) Home Assistant Core release."""
    tag = release.get("tag_name", "") or ""
    return bool(tag) and not release.get("prerelease") and "b" not in tag and "dev" not in tag


async def async_list_core_releases(session: ClientSession) -> list[dict] | None:
    """The last 100 Home Assistant Core releases (None on error). Cached briefly."""
    url = f"{GITHUB_API_BASE}/repos/{HA_CORE_REPO}/releases?per_page=100"
    cached = _cache_get(url)
    if cached is not _MISSING:
        return cached
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception:
        _LOGGER.exception("Failed to list HA releases")
        return None
    releases = [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []
    _release_cache[url] = (time.monotonic(), releases)
    return releases


def _version_tuple(tag: str) -> tuple[int, ...]:
    """Parse a version tag into a comparable tuple, ignoring non-numeric suffixes."""
    clean = tag.lstrip("v")
    parts = []
    for part in clean.split("."):
        match = re.match(r"(\d+)", part)
        if match:
            parts.append(int(match.group(1)))
    return tuple(parts)


def _is_version_between(tag: str, after: str, up_to: str) -> bool:
    """Check if tag is in the range (after, up_to] — exclusive of after, inclusive of up_to."""
    try:
        t = _version_tuple(tag)
        a = _version_tuple(after)
        u = _version_tuple(up_to)
        return a < t <= u
    except (ValueError, IndexError):
        return False


class _HTMLTextExtractor(HTMLParser):
    """Extract text content from HTML, keeping basic structure."""

    def __init__(self) -> None:
        super().__init__()
        self._result: list[str] = []
        self._in_article = False
        self._skip_tags: set[str] = {"script", "style", "nav", "footer", "header"}
        self._skip_depth = 0
        self._heading_tags: set[str] = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "article":
            self._in_article = True
        if tag in self._skip_tags:
            self._skip_depth += 1
        if self._in_article and self._skip_depth == 0:
            if tag in self._heading_tags:
                self._result.append("\n\n## ")
            elif tag == "li":
                self._result.append("\n- ")
            elif tag in ("p", "br", "div"):
                self._result.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "article":
            self._in_article = False
        if tag in self._skip_tags and self._skip_depth > 0:
            self._skip_depth -= 1
        if self._in_article and tag in ("p", "ul", "ol"):
            self._result.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_article and self._skip_depth == 0:
            self._result.append(data)

    def get_text(self) -> str:
        text = "".join(self._result)
        # Collapse excessive whitespace but keep paragraph breaks
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


async def _async_fetch_blog_post(session: ClientSession, url: str) -> str | None:
    """Fetch and extract text from an HA blog post URL."""
    try:
        async with session.get(url, timeout=_BLOG_TIMEOUT) as resp:
            if resp.status != 200:
                _LOGGER.debug("Blog post returned %d: %s", resp.status, url)
                return None
            html = await resp.text()
            extractor = _HTMLTextExtractor()
            extractor.feed(html)
            text = extractor.get_text()
            if len(text) < 100:
                _LOGGER.debug("Blog post extraction too short (%d chars): %s", len(text), url)
                return None
            return text
    except Exception:
        _LOGGER.exception("Failed to fetch blog post: %s", url)
        return None


async def async_get_release_notes(session: ClientSession, repo: str, tag: str) -> str | None:
    """Fetch release notes for a specific tag from a GitHub repo.

    For HA core .0 releases, the GitHub body is just a blog URL — fetch the blog post instead.
    """
    data = await async_get_release(session, repo, tag)
    if data is None:
        return None
    body = data.get("body", "") or ""

    # If body is just a URL (HA .0 releases), fetch the blog post
    stripped = body.strip()
    if stripped.startswith("https://www.home-assistant.io/blog/") and len(stripped) < 200:
        _LOGGER.info("Fetching HA blog post for %s: %s", tag, stripped)
        blog_text = await _async_fetch_blog_post(session, stripped)
        if blog_text:
            return blog_text
        # Fall back to the URL itself if blog fetch fails
        return f"Release notes at: {stripped}"

    return body


async def async_get_ha_release_notes_range(
    session: ClientSession, current_version: str, target_version: str
) -> str | None:
    """Fetch all HA core release notes between current_version and target_version.

    Fetches every release in the range (current_version, target_version] and
    concatenates them in version order.
    """
    # List releases from GitHub (up to 100, shared with the community signal)
    all_releases = await async_list_core_releases(session)
    if all_releases is None:
        return None

    # Filter to stable releases in the version range
    relevant = [
        release
        for release in all_releases
        if is_stable_core_tag(release) and _is_version_between(release["tag_name"], current_version, target_version)
    ]

    if not relevant:
        _LOGGER.warning(
            "No releases found between %s and %s",
            current_version,
            target_version,
        )
        # Fall back to fetching just the target
        return await async_get_release_notes(session, HA_CORE_REPO, target_version)

    # Sort by version ascending
    relevant.sort(key=lambda r: _version_tuple(r["tag_name"]))

    _LOGGER.info(
        "Found %d releases between %s and %s: %s",
        len(relevant),
        current_version,
        target_version,
        ", ".join(r["tag_name"] for r in relevant),
    )

    # Fetch notes for each release
    sections: list[str] = []
    for release in relevant:
        tag = release["tag_name"]
        body = release.get("body", "") or ""

        # If body is just a blog URL, fetch the blog
        stripped = body.strip()
        if stripped.startswith("https://www.home-assistant.io/blog/") and len(stripped) < 200:
            _LOGGER.info("Fetching HA blog post for %s", tag)
            blog_text = await _async_fetch_blog_post(session, stripped)
            if blog_text:
                sections.append(f"# Release {tag}\n\n{blog_text}")
            else:
                sections.append(f"# Release {tag}\n\nRelease notes at: {stripped}")
        elif body:
            sections.append(f"# Release {tag}\n\n{body}")
        else:
            sections.append(f"# Release {tag}\n\nNo release notes available.")

    return "\n\n---\n\n".join(sections)


async def async_get_ha_release_notes(session: ClientSession, version: str) -> str | None:
    """Fetch Home Assistant core release notes for a single version."""
    return await async_get_release_notes(session, HA_CORE_REPO, version)


async def async_get_latest_ha_release(session: ClientSession) -> dict | None:
    """Fetch the latest Home Assistant release info."""
    url = f"{GITHUB_API_BASE}/repos/{HA_CORE_REPO}/releases/latest"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception:
        _LOGGER.exception("Failed to fetch latest HA release")
        return None


async def async_get_hacs_release_notes(session: ClientSession, repo: str, version: str) -> str | None:
    """Fetch release notes for a HACS component.

    Tries the version as-is first, then with 'v' prefix.
    """
    notes = await async_get_release_notes(session, repo, version)
    if notes is None and not version.startswith("v"):
        notes = await async_get_release_notes(session, repo, f"v{version}")
    return notes
