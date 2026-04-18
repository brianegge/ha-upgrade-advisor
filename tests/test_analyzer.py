"""Tests for the analyzer module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor.analyzer import (
    async_analyze,
    build_planning_prompt,
    build_post_upgrade_prompt,
    build_single_pass_prompt,
    build_summary_prompt,
    format_check_pairs,
    parse_post_upgrade_response,
    parse_response,
)


def test_build_prompt_includes_all_sections() -> None:
    """Test prompt includes all required sections."""
    prompt = build_single_pass_prompt(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2024.11.0",
        target_version="2024.12.0",
        release_notes="## Breaking Changes\n- Removed xyz",
        context={
            "integrations": "- hue: Philips Hue",
            "devices": "### hue (5 devices)\n- Extended Color Light (5x): light",
            "automations": "- Turn on lights (on)",
            "addons": "- Mosquitto broker",
        },
        hacs_components="- HACS: 2.0.0",
    )

    assert "Home Assistant Core" in prompt
    assert "2024.11.0" in prompt
    assert "2024.12.0" in prompt
    assert "Removed xyz" in prompt
    assert "hue: Philips Hue" in prompt
    assert "Extended Color Light" in prompt
    assert "Turn on lights" in prompt
    assert "Mosquitto broker" in prompt
    assert "HACS: 2.0.0" in prompt


def test_parse_response_extracts_risk_and_count() -> None:
    """Test parsing risk level and breaking change count from response."""
    response = "Some analysis text.\n\nRISK_LEVEL: High\nBREAKING_CHANGES: 3"
    risk, count = parse_response(response)
    assert risk == "high"
    assert count == 3


def test_parse_response_low_risk() -> None:
    """Test parsing low risk response."""
    response = "All good.\n\nRISK_LEVEL: Low\nBREAKING_CHANGES: 0"
    risk, count = parse_response(response)
    assert risk == "low"
    assert count == 0


def test_parse_response_missing_markers() -> None:
    """Test parsing response without risk/count markers."""
    response = "Some analysis without structured markers."
    risk, count = parse_response(response)
    assert risk == "unknown"
    assert count == 0


def test_parse_response_case_insensitive() -> None:
    """Test parsing is case insensitive for risk level."""
    response = "RISK_LEVEL: medium\nBREAKING_CHANGES: 1"
    risk, count = parse_response(response)
    assert risk == "medium"
    assert count == 1


def test_planning_prompt_targets_bug_shape() -> None:
    """The planner is told to match the bug shape, not the feature keyword."""
    prompt = build_planning_prompt(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2026.4.2",
        target_version="2026.4.3",
        release_notes="Notes",
        context={"integrations": "- foo", "devices": "- bar"},
    )
    assert "unaffected_shape" in prompt
    assert "SPECIFIC MALFORMED SHAPE" in prompt
    # The planner must be told NOT to match every feature keyword
    assert "Matching a common keyword is noise" in prompt


def test_summary_prompt_requires_quoted_lines_and_classification() -> None:
    """The summary prompt forces quoted evidence and downgrade when no malformed line is found."""
    prompt = build_summary_prompt(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2026.4.2",
        target_version="2026.4.3",
        check_results="",
    )
    assert "GROUNDING RULE" in prompt
    assert "likely affected" in prompt
    assert "likely safe" in prompt
    assert "no malformed occurrences detected" in prompt
    assert "Do NOT invent or paraphrase match content" in prompt


def test_build_post_upgrade_prompt_includes_pairs() -> None:
    """Post-upgrade prompt embeds the pre/post check pairs and version info."""
    pairs = format_check_pairs(
        [
            ("Z-Wave cover state", "breaking", "All covers OK", "2 covers unavailable", True, False),
            ("ESPHome lights", "post_upgrade", "none", "0 lights", True, True),
        ]
    )
    prompt = build_post_upgrade_prompt(
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        from_version="2026.4.2",
        target_version="2026.4.3",
        check_pairs=pairs,
    )

    assert "2026.4.2 → 2026.4.3" in prompt
    assert "Z-Wave cover state" in prompt
    assert "BEFORE ✅" in prompt
    assert "AFTER" in prompt
    assert "❌" in prompt
    assert "POST_STATUS" in prompt
    assert "REGRESSIONS" in prompt


def test_parse_post_upgrade_response_extracts_status_and_regressions() -> None:
    """Post-upgrade parser returns status and count."""
    text = "Everything fine.\n\nPOST_STATUS: degraded\nREGRESSIONS: 2"
    status, regressions = parse_post_upgrade_response(text)
    assert status == "degraded"
    assert regressions == 2


def test_parse_post_upgrade_response_defaults_when_missing() -> None:
    """Missing markers produce unknown/0 without raising."""
    status, regressions = parse_post_upgrade_response("nothing structured here")
    assert status == "unknown"
    assert regressions == 0


async def test_async_analyze_success(hass: HomeAssistant, mock_converse) -> None:
    """Test successful analysis returns populated result."""
    result = await async_analyze(
        hass=hass,
        agent_id="conversation.test",
        prompt="test prompt",
        upgrade_type="Home Assistant Core",
        component_name="Home Assistant",
        current_version="2024.11.0",
        target_version="2024.12.0",
    )

    assert result.error is None
    assert result.risk_level == "low"
    assert result.breaking_change_count == 0
    assert "Analysis Report" in result.report
    mock_converse.assert_called_once()


async def test_async_analyze_error_response(hass: HomeAssistant) -> None:
    """Test analysis with error response from AI agent."""
    response = MagicMock()
    response.response.response_type.value = "error"
    response.response.speech = {"plain": {"speech": "Rate limit exceeded"}}

    with patch(
        "homeassistant.components.conversation.async_converse",
        new_callable=AsyncMock,
        return_value=response,
    ):
        result = await async_analyze(
            hass=hass,
            agent_id="conversation.test",
            prompt="test prompt",
            upgrade_type="Home Assistant Core",
            component_name="Home Assistant",
            current_version="2024.11.0",
            target_version="2024.12.0",
        )

    assert result.error == "Rate limit exceeded"
    assert result.report == ""


async def test_async_analyze_exception(hass: HomeAssistant) -> None:
    """Test analysis handles exceptions gracefully."""
    with patch(
        "homeassistant.components.conversation.async_converse",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Connection failed"),
    ):
        result = await async_analyze(
            hass=hass,
            agent_id="conversation.test",
            prompt="test prompt",
            upgrade_type="Home Assistant Core",
            component_name="Home Assistant",
            current_version="2024.11.0",
            target_version="2024.12.0",
        )

    assert result.error is not None
    assert "Connection failed" in result.error
