"""Prompt construction and AI interaction for upgrade analysis."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Phase 1: Ask the LLM to produce structured check tasks
PLANNING_PROMPT = """You are a Home Assistant upgrade advisor. Analyze the release notes \
and produce AUTOMATED CHECKS for this specific installation.

IMPORTANT: The upgrade has NOT been applied yet. All checks run against the \
CURRENT (pre-upgrade) state. Do NOT describe results as "verified post-upgrade" \
— they reflect current working state before any changes are made.

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Release Notes
{release_notes}

## ENABLED Integrations (ONLY these are installed)
{integrations}

## Devices by Integration
{devices}

## HACS Components
{hacs_components}

## CRITICAL RULE
The integrations listed above are the ONLY ones installed. If a breaking change \
or new feature mentions an integration NOT in that list, SKIP IT COMPLETELY. \
Do not create any check for it. Do not mention it. For example, if the release \
notes mention Litter-Robot, Tuya, BMW, JVC Projector, or Roth Touchline but \
none of those appear in the installed list above, do not create checks for them.

## Available Check Types

1. `grep_config` — Search YAML config files and Lovelace dashboards for a pattern
   Params: `pattern` (regex)
   Use for: deprecated config keys, removed options, services that are being changed

2. `automation_references` — Search automation YAML for a pattern
   Params: `pattern` (regex)
   Use for: finding automations using deprecated or newly enhanced services/entities

3. `entity_count` — Count entities for an integration
   Params: `integration` (domain name)
   Use for: confirming an integration is actively used

4. `backup_recent` — Verify a recent backup exists

5. `service_exists` — Check if a specific service exists
   Params: `pattern` (e.g. "light.turn_on")
   Use for: verifying services that may be renamed or removed

## Output

JSON array of check objects with these fields:
- `check`: check type from above
- `title`: short description
- `severity`: "breaking", "warning", or "post_upgrade"
- `context`: why this matters (include the PR number or link from release notes if available)
- `pattern`: regex (for grep_config, automation_references, service_exists)
- `integration`: domain name (for entity_count)
- `if_found`: message if matches found
- `if_not_found`: message if no matches

Create checks in this order:
1. `backup_recent`
2. Breaking changes — ONLY for integrations in the installed list
3. New features / opportunities — for each new feature relevant to an INSTALLED \
   integration, check for existing usage. Use severity "post_upgrade". Include \
   the PR link or release note reference in `context`. Set `if_found` to describe \
   the opportunity.

Output ONLY the JSON array, no other text."""

# Phase 2: Summarize the check results into a final report
SUMMARY_PROMPT = """You are a Home Assistant upgrade advisor. Based on the automated check \
results below, produce a concise PRE-UPGRADE report.

IMPORTANT: The upgrade has NOT been applied yet. All check results reflect the \
CURRENT state BEFORE upgrading. Do NOT say entities were "verified post-upgrade" \
or "confirmed healthy after upgrade" — the upgrade hasn't happened. Instead, say \
things like "currently working" or "X entities active pre-upgrade".

## Upgrade
{upgrade_type}: {component_name} {current_version} → {target_version}

## Automated Check Results
{check_results}

## Instructions
ONLY report on checks that were actually performed. Do not mention integrations \
that were not checked. OMIT any section that would be empty.

Structure the report as follows:

1. **What's New For You** — Lead with this section. List new features and \
   opportunities from the post_upgrade checks, with evidence from the check \
   results. For each, include what's new, which of your devices/automations \
   benefit, and the PR or release note reference from the check context. \
   Only include features for integrations that had checks performed.

2. **Breaking Changes** — Table of breaking change checks with status and \
   evidence. Only include checks that were actually run. If all passed, \
   summarize in one line: "All breaking change checks passed — safe to upgrade."

3. **Action Required** — Only if something ACTUALLY needs to be done \
   before upgrading. Omit if all checks passed.

4. **Risk Assessment** — ONLY if risk is Medium or High. Omit for Low risk.

Keep it short and factual.

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
