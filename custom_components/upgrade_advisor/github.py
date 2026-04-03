"""GitHub API client for fetching release notes."""

from __future__ import annotations

import logging

from aiohttp import ClientSession, ClientTimeout

from .const import GITHUB_API_BASE, HA_CORE_REPO

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = ClientTimeout(total=30)


async def async_get_release_notes(session: ClientSession, repo: str, tag: str) -> str | None:
    """Fetch release notes for a specific tag from a GitHub repo.

    Args:
        session: aiohttp client session.
        repo: GitHub repo in "owner/repo" format.
        tag: Release tag (e.g., "2024.12.0").

    Returns:
        Release notes as markdown, or None if not found.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/releases/tags/{tag}"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 404:
                _LOGGER.debug("No release found for %s tag %s", repo, tag)
                return None
            resp.raise_for_status()
            data = await resp.json()
            return data.get("body", "")
    except Exception:
        _LOGGER.exception("Failed to fetch release notes for %s tag %s", repo, tag)
        return None


async def async_get_ha_release_notes(session: ClientSession, version: str) -> str | None:
    """Fetch Home Assistant core release notes for a version."""
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
