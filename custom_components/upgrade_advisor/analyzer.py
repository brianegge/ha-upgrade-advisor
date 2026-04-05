"""Prompt construction and AI interaction for upgrade analysis."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Phase 1: Ask the LLM to produce structured check tasks
PLANNING_PROMPT = """You are a Home Assistant upgrade advisor. Analyze the release notes below \
and produce a list of AUTOMATED CHECKS to verify whether each breaking change, \
deprecation, or prerequisite actually affects this specific installation.

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Release Notes
{release_notes}

## Installed Integrations
{integrations}

## Devices by Integration
{devices}

## HACS Components
{hacs_components}

## Available Check Types
You can request these automated checks:

1. `grep_config` — Search YAML config files for a pattern
   Params: `pattern` (regex), `files` (glob, default *.yaml)
   Use for: deprecated config keys, removed options, renamed settings

2. `entity_available` — Check if entities for an integration are available
   Params: `integration` (domain name like "mqtt", "zwave_js")
   Use for: verifying integration health before/after upgrade

3. `integration_installed` — Check if an integration is installed
   Params: `integration` (domain name)
   Use for: filtering out breaking changes for uninstalled integrations

4. `automation_references` — Search automation YAML for a pattern
   Params: `pattern` (regex)
   Use for: finding automations that use deprecated services/entities

5. `unavailable_entities` — List unavailable entities, optionally filtered
   Params: `integration` (optional domain filter)
   Use for: pre-upgrade baseline of broken entities

6. `backup_recent` — Verify a recent backup exists
   Params: none
   Use for: pre-upgrade safety check

## Instructions
IMPORTANT: Only create checks for integrations that ARE in the installed list above. \
Skip all breaking changes for integrations that are not installed — do not create \
checks for them and do not mention them.

Output a JSON array of check objects. Each object has:
- `check`: one of the check types above
- `title`: short human-readable description
- `severity`: "breaking", "warning", "info", or "post_upgrade"
- `context`: why this check matters (reference the specific release note change)
- `pattern`: regex pattern (for grep_config and automation_references)
- `integration`: integration domain (for entity_available, integration_installed)
- `if_found`: message if the check finds a match (what the user should do)
- `if_not_found`: message if the check finds no match (why they're safe)

Start with a `backup_recent` check, then checks for each breaking change that \
could affect installed integrations, then a baseline `unavailable_entities` check.

Output ONLY the JSON array, no other text."""

# Phase 2: Summarize the check results into a final report
SUMMARY_PROMPT = """You are a Home Assistant upgrade advisor. Based on the automated check \
results below, produce a concise upgrade report.

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Automated Check Results
{check_results}

## Instructions
IMPORTANT: Only report on checks that were actually performed. Do not add \
information about integrations or breaking changes that were not checked.

Produce a concise report. Include ONLY these sections, and OMIT any section \
that would just say "None":
1. **Pre-Upgrade Status** — results of backup check and current system health
2. **Breaking Changes Verified** — for each check, state whether this installation \
IS or IS NOT affected, with the specific evidence (e.g., "Searched 15 YAML files, \
no object_id found" or "Found object_id in mqtt.yaml line 42")
3. **Action Required** — only if the user ACTUALLY needs to do something. \
If all checks passed, say "No action required — safe to upgrade."
4. **Risk Assessment** — Low/Medium/High based on actual check results, not speculation

Omit sections like "Prerequisites: None" or "Deprecations: None" — if there's \
nothing to report, don't include the section at all.

Keep it short and factual. Lead with verdicts, not explanations.

End your response with:
RISK_LEVEL: <Low|Medium|High>
BREAKING_CHANGES: <number of checks that FAILED>"""

# Legacy single-pass prompt (fallback)
SINGLE_PASS_PROMPT = """You are a Home Assistant upgrade advisor. Analyze the following release notes \
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
IMPORTANT: Only include items that affect THIS installation. Do NOT mention \
integrations, devices, or services that are not present in the lists above. \
If a breaking change applies to an integration this user does not have installed, \
skip it entirely — do not include it with a "not affected" note.

Produce a report with these sections:
1. **Breaking Changes** — changes that WILL affect this installation. Only list \
changes for integrations/platforms that appear in the installed integrations or \
devices lists above. Omit all others completely.
2. **Prerequisites** — things that must be done BEFORE upgrading
3. **Deprecations** — things that still work but should be migrated, only for \
installed integrations
4. **New Features** — relevant new capabilities for installed integrations only
5. **Recommended Actions** — ordered checklist of what to do before/after upgrading
6. **Risk Assessment** — Low/Medium/High with brief justification

If there are no breaking changes for this installation, say so clearly and briefly.

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


def build_planning_prompt(
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
    release_notes: str,
    context: dict[str, Any],
    hacs_components: str = "N/A",
) -> str:
    """Build the phase 1 planning prompt."""
    return PLANNING_PROMPT.format(
        upgrade_type=upgrade_type,
        component_name=component_name,
        current_version=current_version,
        target_version=target_version,
        release_notes=release_notes or "No release notes available.",
        integrations=context.get("integrations", "No integrations found."),
        devices=context.get("devices", "No devices found."),
        hacs_components=hacs_components,
    )


def build_summary_prompt(
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
    check_results: str,
) -> str:
    """Build the phase 2 summary prompt."""
    return SUMMARY_PROMPT.format(
        upgrade_type=upgrade_type,
        component_name=component_name,
        current_version=current_version,
        target_version=target_version,
        check_results=check_results,
    )


def build_single_pass_prompt(
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
    release_notes: str,
    context: dict[str, Any],
    hacs_components: str = "N/A",
) -> str:
    """Build the legacy single-pass prompt (fallback for HACS components)."""
    return SINGLE_PASS_PROMPT.format(
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
    """Parse the AI response to extract risk level and breaking change count."""
    risk_level = "unknown"
    breaking_count = 0

    risk_match = re.search(r"RISK_LEVEL:\s*(Low|Medium|High)", response_text, re.IGNORECASE)
    if risk_match:
        risk_level = risk_match.group(1).lower()

    count_match = re.search(r"BREAKING_CHANGES:\s*(\d+)", response_text)
    if count_match:
        breaking_count = int(count_match.group(1))

    return risk_level, breaking_count


async def async_converse_with_agent(hass: HomeAssistant, agent_id: str, prompt: str) -> str:
    """Send a prompt to the AI agent and return the response text."""
    from homeassistant.components.conversation import async_converse
    from homeassistant.core import Context

    response = await async_converse(
        hass=hass,
        text=prompt,
        conversation_id=None,
        context=Context(),
        agent_id=agent_id,
    )

    if response.response.response_type.value == "error":
        error_msg = response.response.speech.get("plain", {}).get("speech", "Unknown error")
        raise RuntimeError(error_msg)

    return response.response.speech.get("plain", {}).get("speech", "")


async def async_analyze(
    hass: HomeAssistant,
    agent_id: str,
    prompt: str,
    upgrade_type: str,
    component_name: str,
    current_version: str,
    target_version: str,
) -> AnalysisResult:
    """Send a prompt to the AI agent and parse the response (single-pass)."""
    try:
        report_text = await async_converse_with_agent(hass, agent_id, prompt)
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
