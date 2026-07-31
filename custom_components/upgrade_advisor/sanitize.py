"""Sanitizers for untrusted LLM output.

Release notes are third-party text, so everything the model writes after
reading them is attacker-influenced. Two sinks need protection:

* the report, which is published as a state attribute and rendered in a
  Lovelace Markdown card — Home Assistant's markdown pipeline permits
  `<img src="https://...">`, so an image the model was steered to emit
  becomes an outbound request from the viewer's browser;
* check-task text (`title`, `context`, `if_found`, `if_not_found`), which
  is echoed back into the phase-3 prompt and would otherwise carry an
  injected instruction across the phase boundary.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Link targets we are willing to render. Anything else is reduced to plain
# text so it cannot be fetched or clicked.
ALLOWED_LINK_DOMAINS = ("github.com", "home-assistant.io")

_MARKDOWN_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
# Single capture for the whole target; the URL is split out in code. Two
# adjacent quantifiers here would backtrack superlinearly on an unterminated
# "(" in model-authored text.
_MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_HTML_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
_BARE_URL = re.compile(r"(?<![(\w])\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>()\[\]]+")

# Characters that browsers and urlparse disagree about. A backslash is a path
# separator per WHATWG but part of the authority to urlparse, so
# `https://attacker.example\@github.com/` looks allowlisted here while a
# browser resolves attacker.example. Refuse rather than try to out-parse them.
_URL_PARSER_HAZARDS = frozenset("\\\t\r\n <>\"'")


def is_allowed_url(url: str) -> bool:
    """True if the URL is http(s) on an allowlisted domain."""
    candidate = url.strip()
    if not candidate or any(char in _URL_PARSER_HAZARDS for char in candidate):
        return False
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in candidate):
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in ALLOWED_LINK_DOMAINS)


def _drop_images(text: str) -> str:
    """Remove markdown images, keeping only their alt text."""
    return _MARKDOWN_IMAGE.sub(r"\1", text)


def _filter_links(text: str, *, keep_allowed: bool) -> str:
    """Reduce markdown links to plain text unless the target is allowlisted."""

    def replace(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        # A markdown target may carry a title: [text](url "title").
        url = target.split()[0] if target.split() else ""
        if keep_allowed and is_allowed_url(url):
            return match.group(0)
        return label

    return _MARKDOWN_LINK.sub(replace, text)


def _defang_bare_urls(text: str, *, keep_allowed: bool) -> str:
    """Wrap non-allowlisted bare URLs in backticks so they are not autolinked."""

    def replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if keep_allowed and is_allowed_url(url):
            return url
        return f"`{url}`"

    return _BARE_URL.sub(replace, text)


def sanitize_report(report: str) -> str:
    """Make an LLM-authored markdown report safe to store and render.

    Images are removed outright (they issue a request the moment the card
    renders). Links and bare URLs survive only if they point at an
    allowlisted domain; everything else is reduced to inert text. Raw HTML
    is stripped — the integration adds its own anchors after this runs.
    """
    if not report:
        return report
    cleaned = _drop_images(report)
    cleaned = _filter_links(cleaned, keep_allowed=True)
    cleaned = _HTML_TAG.sub("", cleaned)
    return _defang_bare_urls(cleaned, keep_allowed=True)


def strip_markup(text: str) -> str:
    """Flatten model-supplied short text (check titles, if_found, ...).

    These strings are echoed into a later prompt, so they keep no markup at
    all: no images, no links, no HTML, and every URL defanged.
    """
    if not text:
        return text
    cleaned = _drop_images(text)
    cleaned = _filter_links(cleaned, keep_allowed=False)
    cleaned = _HTML_TAG.sub("", cleaned)
    return _defang_bare_urls(cleaned, keep_allowed=False)
