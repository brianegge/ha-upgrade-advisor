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

Each integration section shows "entities by domain" — use this to verify a \
domain-specific feature applies. If the release note talks about covers but \
the integration has no `cover` entities, skip it.

## HACS Components
{hacs_components}

## CRITICAL RULE
The integrations listed above are the ONLY ones installed. If a breaking change \
or new feature mentions an integration NOT in that list, SKIP IT COMPLETELY. \
Do not create any check for it. Do not mention it. For example, if the release \
notes mention Litter-Robot, Tuya, BMW, JVC Projector, or Roth Touchline but \
none of those appear in the installed list above, do not create checks for them.

Similarly, if a feature applies only to a specific entity domain (e.g. lights, \
covers, climate), confirm the installed integration actually has entities in \
that domain before creating a check. Check the "entities by domain" line above, \
or use `entity_count` with both `integration` and `domain` set.

## Available Check Types

1. `grep_config` — Search YAML config files and Lovelace dashboards for a pattern
   Params: `pattern` (regex), optional `unaffected_shape` (regex)
   Use for: deprecated config keys, removed options, services that are being changed.

   CRITICAL: `pattern` must target the SPECIFIC MALFORMED SHAPE the fix is \
   about — not the surrounding feature keyword. A fix for "device_class \
   stripped on reload for template binary sensors" should NOT match every \
   `device_class:` line. Instead it should match the shape that triggers \
   the bug (e.g. `device_class:\\s*$` or `device_class:\\s*(null|none)\\b`). \
   Matching a common keyword is noise; matching the bug shape is signal.

   If you cannot narrow the regex to the bug shape, set `unaffected_shape` \
   to a regex that matches a WELL-FORMED occurrence. Lines that also match \
   `unaffected_shape` will be discarded as benign. For the example above, \
   `unaffected_shape` could be `device_class:\\s*\\w+` (has a non-empty value). \
   The goal: the reported count is "lines that may actually be hit by the \
   bug", not "lines that use the feature."

2. `automation_references` — Search automation YAML for a pattern
   Params: `pattern` (regex)
   Use for: finding automations using deprecated or newly enhanced services/entities

3. `entity_count` — Count entities for an integration
   Params: `integration` (integration domain, e.g. "esphome"), optional `domain`
   (entity domain, e.g. "light", "cover", "sensor")
   Use for: confirming an integration is actively used. For features that only
   apply to a specific entity domain (light color temperature, cover state,
   climate presets, etc.), ALWAYS pass `domain` to narrow the check — an
   integration having many entities overall does not mean it has entities of
   the relevant domain.

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
- `unaffected_shape`: optional regex for grep_config — lines matching this are \
  filtered out as benign
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

GROUNDING RULE for grep_config hits — when a grep_config check reports \
matches, you MUST:
- Quote up to 3 of the matched lines VERBATIM (file:line: content) from the \
  check detail. These are the concrete evidence.
- For each quoted line, classify it as one of:
  - **likely affected** — the line shape matches the bug described in the release notes
  - **likely safe** — the line uses the feature but not the bug shape
  - **unclear** — cannot tell without reviewing the surrounding YAML
- If you cannot point at any line you'd label "likely affected," downgrade \
  the check from breaking/warning to INFO and say so plainly: \
  "Feature is in use; no malformed occurrences detected in {{N}} matches reviewed." \
  Do NOT pad with vague warnings like "review these to ensure they are \
  correctly formed" — that is noise, not advice.

Do NOT invent or paraphrase match content. If the detail says `foo.yaml:42: \
bar: baz`, quote it as `foo.yaml:42: bar: baz`. Do not summarize it as "a \
reference to bar."

Structure the report as follows:

1. **What's New For You** — Lead with this section. List new features and \
   opportunities from the post_upgrade checks, with evidence from the check \
   results. For each, include what's new, which of your devices/automations \
   benefit, and the PR or release note reference from the check context. \
   Only include features for integrations that had checks performed.

2. **Breaking Changes** — Table of breaking change checks with status and \
   evidence. Only include checks that were actually run. For grep_config \
   hits, include the quoted lines + classifications per the grounding rule \
   above. If all passed, summarize in one line: "All breaking change checks \
   passed — safe to upgrade."

3. **Action Required** — Only if something ACTUALLY needs to be done \
   before upgrading. Must reference a specific "likely affected" line. \
   Omit if nothing is concretely affected.

4. **Risk Assessment** — ONLY if risk is Medium or High. Omit for Low risk.

Keep it short and factual.

End your response with:
RISK_LEVEL: <Low|Medium|High>
BREAKING_CHANGES: <number of checks that FAILED>"""

# Post-upgrade: re-run the same checks and describe the delta
POST_UPGRADE_SUMMARY_PROMPT = """You are a Home Assistant upgrade advisor. The upgrade has \
ALREADY BEEN APPLIED. Compare the pre-upgrade and post-upgrade check results \
and describe what changed.

## Upgrade (completed)
{upgrade_type}: {component_name} {from_version} → {target_version}

## Check Results (before → after)
{check_pairs}

## Instructions
Classify each check pair:
- OK — passed before, still passes now. Do not list individually; count them.
- NEWLY FAILING — passed before, fails now. This is almost certainly caused \
  by the upgrade. List these prominently.
- STILL FAILING — failed before and after. Pre-existing; do NOT blame the upgrade.
- IMPROVED — failed before, passes now. The upgrade or a migration fixed it.
- OPPORTUNITY — post_upgrade severity checks that now show adoption or can \
  be adopted.

Report structure (OMIT any empty section):

1. **Regressions caused by this upgrade** — only NEWLY FAILING checks. For \
   each, include the check title, what changed, and a suggested next step.
2. **Improvements** — only IMPROVED checks worth surfacing.
3. **Opportunities now available** — for OPPORTUNITY checks, describe the new \
   capability and how to adopt it.
4. **Pre-existing issues unchanged** — a brief note if there are STILL FAILING \
   checks; do NOT re-describe them in detail.
5. **Summary** — one line: did the upgrade land cleanly?

Keep it short and factual. Do not invent findings that aren't in the check data.

End your response with:
POST_STATUS: <clean|degraded|broken>
REGRESSIONS: <number of NEWLY FAILING checks>"""


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
    # Populated by the two-phase pipeline so the coordinator can persist the
    # plan for a post-upgrade replay. Not serialized to the user-facing report.
    check_tasks: list[Any] | None = None
    check_results: list[Any] | None = None


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


def parse_post_upgrade_response(response_text: str) -> tuple[str, int]:
    """Parse the post-upgrade AI response: POST_STATUS + REGRESSIONS count."""
    status = "unknown"
    regressions = 0

    status_match = re.search(r"POST_STATUS:\s*(clean|degraded|broken)", response_text, re.IGNORECASE)
    if status_match:
        status = status_match.group(1).lower()

    count_match = re.search(r"REGRESSIONS:\s*(\d+)", response_text)
    if count_match:
        regressions = int(count_match.group(1))

    return status, regressions


def format_check_pairs(pairs: list[tuple[str, str, str, str, bool, bool]]) -> str:
    """Render pre/post check result pairs for the post-upgrade prompt.

    Each pair is (title, severity, pre_detail, post_detail, pre_passed, post_passed).
    """
    lines: list[str] = []
    for title, severity, pre_detail, post_detail, pre_passed, post_passed in pairs:
        pre_icon = "✅" if pre_passed else "❌"
        post_icon = "✅" if post_passed else "❌"
        lines.append(f"**{title}** [{severity}]")
        lines.append(f"  BEFORE {pre_icon}: {pre_detail}")
        lines.append(f"  AFTER  {post_icon}: {post_detail}")
        lines.append("")
    return "\n".join(lines)


def build_post_upgrade_prompt(
    upgrade_type: str,
    component_name: str,
    from_version: str,
    target_version: str,
    check_pairs: str,
) -> str:
    """Build the post-upgrade summary prompt."""
    return POST_UPGRADE_SUMMARY_PROMPT.format(
        upgrade_type=upgrade_type,
        component_name=component_name,
        from_version=from_version,
        target_version=target_version,
        check_pairs=check_pairs,
    )


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
