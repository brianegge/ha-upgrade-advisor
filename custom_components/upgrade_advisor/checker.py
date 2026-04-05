"""Automated verification checks for upgrade breaking changes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Result of a single verification check."""

    check_id: str
    title: str
    passed: bool
    detail: str
    severity: str = "info"  # breaking, warning, info, post_upgrade


@dataclass
class CheckTask:
    """A structured check to perform."""

    check: str
    title: str
    severity: str = "info"
    context: str = ""
    if_found: str = ""
    if_not_found: str = ""
    # Check-specific params
    pattern: str = ""
    files: str = "*.yaml"
    integration: str = ""
    entity_id: str = ""
    component: str = ""


def parse_check_tasks(raw_json: str) -> list[CheckTask]:
    """Parse LLM output into structured check tasks."""
    # Extract JSON array from the response (LLM may wrap it in markdown)
    match = re.search(r"\[.*\]", raw_json, re.DOTALL)
    if not match:
        _LOGGER.warning("No JSON array found in LLM check output")
        return []

    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        _LOGGER.warning("Failed to parse check tasks JSON")
        return []

    tasks: list[CheckTask] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        tasks.append(
            CheckTask(
                check=item.get("check", "unknown"),
                title=item.get("title", f"Check {i + 1}"),
                severity=item.get("severity", "info"),
                context=item.get("context", ""),
                if_found=item.get("if_found", ""),
                if_not_found=item.get("if_not_found", ""),
                pattern=item.get("pattern", ""),
                files=item.get("files", "*.yaml"),
                integration=item.get("integration", ""),
                entity_id=item.get("entity_id", ""),
                component=item.get("component", ""),
            )
        )
    return tasks


async def async_run_checks(hass: HomeAssistant, tasks: list[CheckTask]) -> list[CheckResult]:
    """Execute all check tasks and return results."""
    results: list[CheckResult] = []
    for task in tasks:
        try:
            result = await _run_single_check(hass, task)
            results.append(result)
        except Exception:
            _LOGGER.exception("Check failed: %s", task.title)
            results.append(
                CheckResult(
                    check_id=task.check,
                    title=task.title,
                    passed=False,
                    detail="Check failed with an error",
                    severity=task.severity,
                )
            )
    return results


async def _run_single_check(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Run a single check task."""
    dispatch = {
        "grep_config": _check_grep_config,
        "entity_available": _check_entity_available,
        "integration_installed": _check_integration_installed,
        "automation_references": _check_automation_references,
        "unavailable_entities": _check_unavailable_entities,
        "backup_recent": _check_backup_recent,
        "service_exists": _check_service_exists,
        "entity_count": _check_entity_count,
    }

    handler = dispatch.get(task.check)
    if handler is None:
        return CheckResult(
            check_id=task.check,
            title=task.title,
            passed=True,
            detail=f"Unknown check type '{task.check}' — skipped",
            severity="info",
        )

    return await handler(hass, task)


async def _check_grep_config(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Search HA config YAML files for a pattern."""
    config_dir = Path(hass.config.path())
    pattern = task.pattern
    if not pattern:
        return CheckResult(
            check_id="grep_config",
            title=task.title,
            passed=True,
            detail="No pattern specified",
            severity=task.severity,
        )

    regex = re.compile(pattern, re.IGNORECASE)
    matches: list[str] = []

    # Search YAML files in config directory (non-recursive for top-level,
    # recursive for packages/ and integrations/)
    yaml_files = list(config_dir.glob("*.yaml"))
    yaml_files.extend(config_dir.glob("packages/**/*.yaml"))
    yaml_files.extend(config_dir.glob("integrations/**/*.yaml"))
    yaml_files.extend(config_dir.glob("mqtt/**/*.yaml"))

    # Also check specific subdirs that might have MQTT config
    for subdir in ["", "packages", "integrations", "mqtt"]:
        subpath = config_dir / subdir if subdir else config_dir
        if subpath.is_dir():
            for yaml_file in subpath.glob("*.yaml"):
                if yaml_file not in yaml_files:
                    yaml_files.append(yaml_file)

    files_searched = 0
    for yaml_file in yaml_files:
        # Skip huge files and hidden files
        if yaml_file.name.startswith(".") or yaml_file.stat().st_size > 1_000_000:
            continue
        try:
            content = await hass.async_add_executor_job(yaml_file.read_text)
            files_searched += 1
            for line_num, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    relative = yaml_file.relative_to(config_dir)
                    matches.append(f"{relative}:{line_num}: {line.strip()}")
        except Exception:
            continue

    if matches:
        match_text = "\n".join(f"  - {m}" for m in matches[:10])
        extra = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
        return CheckResult(
            check_id="grep_config",
            title=task.title,
            passed=False,
            detail=(
                f"Found '{pattern}' in {len(matches)} location(s) "
                f"across {files_searched} files:\n{match_text}{extra}"
                f"\n\n{task.if_found}"
            ),
            severity=task.severity,
        )

    return CheckResult(
        check_id="grep_config",
        title=task.title,
        passed=True,
        detail=f"Searched {files_searched} YAML files — no matches for '{pattern}'.\n\n{task.if_not_found}",
        severity=task.severity,
    )


async def _check_entity_available(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Check if entities for an integration are available."""
    integration = task.integration
    if not integration:
        return CheckResult(
            check_id="entity_available",
            title=task.title,
            passed=True,
            detail="No integration specified",
            severity=task.severity,
        )

    all_states = hass.states.async_all()
    total = 0
    unavailable = 0
    unavailable_list: list[str] = []

    for state in all_states:
        # Match by platform (entity_id prefix) or by checking config entries
        if integration in state.entity_id or state.attributes.get("platform") == integration:
            total += 1
            if state.state == "unavailable":
                unavailable += 1
                unavailable_list.append(state.entity_id)

    if total == 0:
        return CheckResult(
            check_id="entity_available",
            title=task.title,
            passed=True,
            detail=f"No entities found for '{integration}'",
            severity=task.severity,
        )

    if unavailable > 0:
        sample = ", ".join(unavailable_list[:5])
        extra = f" and {unavailable - 5} more" if unavailable > 5 else ""
        return CheckResult(
            check_id="entity_available",
            title=task.title,
            passed=False,
            detail=f"{unavailable}/{total} entities unavailable for '{integration}': {sample}{extra}",
            severity=task.severity,
        )

    return CheckResult(
        check_id="entity_available",
        title=task.title,
        passed=True,
        detail=f"All {total} entities for '{integration}' are available",
        severity=task.severity,
    )


async def _check_integration_installed(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Check if a specific integration is installed."""
    integration = task.integration
    entries = hass.config_entries.async_entries()
    installed = any(e.domain == integration for e in entries)

    if installed:
        return CheckResult(
            check_id="integration_installed",
            title=task.title,
            passed=True,
            detail=f"Integration '{integration}' is installed.\n\n{task.if_found}",
            severity=task.severity,
        )

    return CheckResult(
        check_id="integration_installed",
        title=task.title,
        passed=True,  # Not installed = not affected = pass
        detail=f"Integration '{integration}' is not installed — not affected.\n\n{task.if_not_found}",
        severity=task.severity,
    )


async def _check_automation_references(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Check if any automations reference a pattern (entity, service, etc.)."""
    pattern = task.pattern
    if not pattern:
        return CheckResult(
            check_id="automation_references",
            title=task.title,
            passed=True,
            detail="No pattern specified",
            severity=task.severity,
        )

    # Search automation YAML files
    config_dir = Path(hass.config.path())
    regex = re.compile(pattern, re.IGNORECASE)
    matches: list[str] = []

    # Check automations.yaml and any automation includes
    auto_files = list(config_dir.glob("automations.yaml"))
    auto_files.extend(config_dir.glob("automations/*.yaml"))
    auto_files.extend(config_dir.glob("packages/**/*.yaml"))

    for yaml_file in auto_files:
        try:
            content = await hass.async_add_executor_job(yaml_file.read_text)
            for line_num, line in enumerate(content.split("\n"), 1):
                if regex.search(line):
                    relative = yaml_file.relative_to(config_dir)
                    matches.append(f"{relative}:{line_num}: {line.strip()}")
        except Exception:
            continue

    # Also check automation entity states for friendly names matching pattern
    auto_states = hass.states.async_all("automation")
    entity_matches: list[str] = []
    for state in auto_states:
        friendly = state.attributes.get("friendly_name", "")
        if regex.search(friendly) or regex.search(state.entity_id):
            entity_matches.append(friendly or state.entity_id)

    all_matches = matches + [f"automation: {m}" for m in entity_matches]

    if all_matches:
        match_text = "\n".join(f"  - {m}" for m in all_matches[:10])
        return CheckResult(
            check_id="automation_references",
            title=task.title,
            passed=False,
            detail=f"Found {len(all_matches)} reference(s) to '{pattern}':\n{match_text}\n\n{task.if_found}",
            severity=task.severity,
        )

    return CheckResult(
        check_id="automation_references",
        title=task.title,
        passed=True,
        detail=f"No references to '{pattern}' found in automations.\n\n{task.if_not_found}",
        severity=task.severity,
    )


async def _check_unavailable_entities(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Count unavailable entities, optionally filtered by integration."""
    integration = task.integration
    all_states = hass.states.async_all()

    unavailable: list[str] = []
    for state in all_states:
        if state.state != "unavailable":
            continue
        if integration and integration not in state.entity_id:
            continue
        unavailable.append(f"{state.entity_id} ({state.attributes.get('friendly_name', '')})")

    scope = f" for '{integration}'" if integration else ""
    if unavailable:
        sample = "\n".join(f"  - {e}" for e in unavailable[:10])
        extra = f"\n  ... and {len(unavailable) - 10} more" if len(unavailable) > 10 else ""
        return CheckResult(
            check_id="unavailable_entities",
            title=task.title,
            passed=False,
            detail=f"{len(unavailable)} unavailable entities{scope}:\n{sample}{extra}",
            severity=task.severity,
        )

    return CheckResult(
        check_id="unavailable_entities",
        title=task.title,
        passed=True,
        detail=f"No unavailable entities{scope}",
        severity=task.severity,
    )


async def _check_backup_recent(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Check if a recent backup exists."""
    backup_states = hass.states.async_all("sensor")
    for state in backup_states:
        if (
            "backup" in state.entity_id
            and "last" in state.entity_id
            and state.state not in ("unavailable", "unknown", "")
        ):
            return CheckResult(
                check_id="backup_recent",
                title=task.title,
                passed=True,
                detail=f"Last backup: {state.state}",
                severity=task.severity,
            )

    return CheckResult(
        check_id="backup_recent",
        title=task.title,
        passed=False,
        detail="Could not verify a recent backup. Create a backup before upgrading.",
        severity="warning",
    )


async def _check_service_exists(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Check if a specific service exists."""
    pattern = task.pattern
    if not pattern or "." not in pattern:
        return CheckResult(
            check_id="service_exists",
            title=task.title,
            passed=True,
            detail="No service specified",
            severity=task.severity,
        )

    domain, service = pattern.split(".", 1)
    exists = hass.services.has_service(domain, service)

    return CheckResult(
        check_id="service_exists",
        title=task.title,
        passed=exists,
        detail=f"Service '{pattern}' {'exists' if exists else 'does not exist'}",
        severity=task.severity,
    )


async def _check_entity_count(hass: HomeAssistant, task: CheckTask) -> CheckResult:
    """Count entities for an integration."""
    integration = task.integration
    if not integration:
        return CheckResult(
            check_id="entity_count",
            title=task.title,
            passed=True,
            detail="No integration specified",
            severity=task.severity,
        )

    count = sum(1 for s in hass.states.async_all() if integration in s.entity_id)

    return CheckResult(
        check_id="entity_count",
        title=task.title,
        passed=True,
        detail=f"Found {count} entities for '{integration}'",
        severity=task.severity,
    )


def format_check_results(results: list[CheckResult]) -> str:
    """Format check results as markdown for the AI summary prompt."""
    lines: list[str] = []
    for r in results:
        icon = "✅" if r.passed else "❌"
        lines.append(f"{icon} **{r.title}** [{r.severity}]")
        lines.append(f"   {r.detail}")
        lines.append("")
    return "\n".join(lines)
