"""GitHub API client for fetching release notes."""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

from aiohttp import ClientSession, ClientTimeout

from .const import GITHUB_API_BASE, HA_CORE_REPO

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = ClientTimeout(total=30)
_BLOG_TIMEOUT = ClientTimeout(total=60)


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
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 404:
                _LOGGER.debug("No release found for %s tag %s", repo, tag)
                return None
            resp.raise_for_status()
            data = await resp.json()
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
    except Exception:
        _LOGGER.exception("Failed to fetch release notes for %s tag %s", repo, tag)
        return None


async def async_get_ha_release_notes_range(
    session: ClientSession, current_version: str, target_version: str
) -> str | None:
    """Fetch all HA core release notes between current_version and target_version.

    Fetches every release in the range (current_version, target_version] and
    concatenates them in version order.
    """
    # List releases from GitHub (paginated, up to 100)
    releases_url = f"{GITHUB_API_BASE}/repos/{HA_CORE_REPO}/releases?per_page=100"
    try:
        async with session.get(releases_url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            all_releases = await resp.json()
    except Exception:
        _LOGGER.exception("Failed to list HA releases")
        return None

    # Filter to stable releases in the version range
    relevant: list[dict] = []
    for release in all_releases:
        tag = release.get("tag_name", "")
        if release.get("prerelease") or "b" in tag or "dev" in tag:
            continue
        if _is_version_between(tag, current_version, target_version):
            relevant.append(release)

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
