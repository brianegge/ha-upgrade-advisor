"""Prompt construction and AI interaction for upgrade analysis."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a Home Assistant upgrade advisor. Analyze the following release notes \
against this user's installation and produce a report.

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Release Notes
{release_notes}

## Installed Integrations
{integrations}

## Devices by Integration
{devices}

## Automations
{automations}

## Add-ons
{addons}

## HACS Components
{hacs_components}

## Instructions
Produce a report with these sections:
1. **Breaking Changes** — changes that WILL affect this installation, with specific \
entity/device/automation references where possible
2. **Prerequisites** — things that must be done BEFORE upgrading (especially for \
HACS components that document prerequisites in their release notes)
3. **Deprecations** — things that still work but should be migrated
4. **New Features** — relevant new capabilities for installed integrations
5. **Recommended Actions** — ordered checklist of what to do before/after upgrading
6. **Risk Assessment** — Low/Medium/High with brief justification

For each breaking change, include:
- Which integration/component is affected
- What specifically breaks
- What action the user must take
- Whether it must be done before or after the upgrade

End your response with a line in this exact format:
RISK_LEVEL: <Low|Medium|High>
BREAKING_CHANGES: <number>"""


@dataclass
class AnalysisResult:
    """Result of an upgrade analysis."""

    report: str
    risk_level: str = "unknown"
    breaking_change_count: int = 0
    upgrade_type: str = ""
    component_name: str = ""
    current_version: str = ""
    target_version: str = ""
    error: str | None = None


def build_prompt(
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
    release_notes: str,
    context: dict[str, Any],
    hacs_components: str = "N/A",
) -> str:
    """Build the analysis prompt from gathered data."""
    return PROMPT_TEMPLATE.format(
        upgrade_type=upgrade_type,
        component_name=component_name,
        current_version=current_version,
        target_version=target_version,
        release_notes=release_notes or "No release notes available.",
        integrations=context.get("integrations", "No integrations found."),
        devices=context.get("devices", "No devices found."),
        automations=context.get("automations", "No automations found."),
        addons=context.get("addons", "No add-ons found."),
        hacs_components=hacs_components,
    )


def parse_response(response_text: str) -> tuple[str, int]:
    """Parse the AI response to extract risk level and breaking change count.

    Returns:
        Tuple of (risk_level, breaking_change_count).
    """
    risk_level = "unknown"
    breaking_count = 0

    risk_match = re.search(r"RISK_LEVEL:\s*(Low|Medium|High)", response_text, re.IGNORECASE)
    if risk_match:
        risk_level = risk_match.group(1).lower()

    count_match = re.search(r"BREAKING_CHANGES:\s*(\d+)", response_text)
    if count_match:
        breaking_count = int(count_match.group(1))

    return risk_level, breaking_count


async def async_analyze(
    hass: HomeAssistant,
    agent_id: str,
    prompt: str,
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
) -> AnalysisResult:
    """Send the prompt to the AI agent and parse the response."""
    try:
        from homeassistant.components.conversation import async_converse

        response = await async_converse(
            hass=hass,
            text=prompt,
            conversation_id=None,
            agent_id=agent_id,
        )

        if response.response.response_type.value == "error":
            error_msg = response.response.speech.get("plain", {}).get("speech", "Unknown error")
            return AnalysisResult(
                report="",
                error=error_msg,
                upgrade_type=upgrade_type,
                component_name=component_name,
                current_version=current_version,
                target_version=target_version,
            )

        report_text = response.response.speech.get("plain", {}).get("speech", "")
        risk_level, breaking_count = parse_response(report_text)

        return AnalysisResult(
            report=report_text,
            risk_level=risk_level,
            breaking_change_count=breaking_count,
            upgrade_type=upgrade_type,
            component_name=component_name,
            current_version=current_version,
            target_version=target_version,
        )

    except Exception as err:
        _LOGGER.exception("Analysis failed")
        return AnalysisResult(
            report="",
            error=str(err),
            upgrade_type=upgrade_type,
            component_name=component_name,
            current_version=current_version,
            target_version=target_version,
        )
