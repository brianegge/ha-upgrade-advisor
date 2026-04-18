"""Automated verification checks for upgrade breaking changes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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
    # Optional second regex applied to each line that already matched `pattern`.
    # Lines that match `unaffected_shape` are dropped from the result — they
    # represent the feature being used correctly, not the bug shape the fix
    # is about. Use this to separate "uses the feature" from "hit by the bug."
    unaffected_shape: str = ""
    files: str = "*.yaml"
    integration: str = ""
    domain: str = ""
    entity_id: str = ""
    component: str = ""


def check_task_to_dict(task: CheckTask) -> dict:
    """Serialize a CheckTask for persistent storage."""
    return asdict(task)


def check_task_from_dict(data: dict) -> CheckTask:
    """Rebuild a CheckTask from persisted state, ignoring unknown fields."""
    valid = {f.name for f in fields(CheckTask)}
    return CheckTask(**{k: v for k, v in data.items() if k in valid})


def check_result_to_dict(result: CheckResult) -> dict:
    """Serialize a CheckResult for persistent storage."""
    return asdict(result)


def check_result_from_dict(data: dict) -> CheckResult:
    """Rebuild a CheckResult from persisted state, ignoring unknown fields."""
    valid = {f.name for f in fields(CheckResult)}
    return CheckResult(**{k: v for k, v in data.items() if k in valid})


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
                unaffected_shape=item.get("unaffected_shape", ""),
                files=item.get("files", "*.yaml"),
                integration=item.get("integration", ""),
                domain=item.get("domain", ""),
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


_MAX_FILE_SIZE_BYTES = 2_000_000


def _grep_files_sync(
    config_dir: Path,
    glob_patterns: list[str],
    extra_glob_roots: list[tuple[str, str]],
    regex: re.Pattern[str],
    disqualifier: re.Pattern[str] | None,
) -> tuple[list[str], int, int]:
    """Walk the config tree and grep matching lines. Runs in an executor.

    extra_glob_roots is a list of (subdir, pattern) tuples — the subdir is
    probed via is_dir() and only globbed if present. Returns
    (matches, disqualified_count, files_searched).
    """
    search_files: list[Path] = []
    for pattern in glob_patterns:
        search_files.extend(config_dir.glob(pattern))
    for subdir, pattern in extra_glob_roots:
        root = config_dir / subdir
        if root.is_dir():
            search_files.extend(root.glob(pattern))

    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in search_files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)

    matches: list[str] = []
    disqualified = 0
    files_searched = 0
    for search_file in unique_files:
        try:
            if search_file.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue
            content = search_file.read_text()
        except OSError:
            continue
        files_searched += 1
        try:
            relative = search_file.relative_to(config_dir)
        except ValueError:
            relative = search_file
        for line_num, line in enumerate(content.split("\n"), 1):
            if not regex.search(line):
                continue
            if disqualifier is not None and disqualifier.search(line):
                disqualified += 1
                continue
            matches.append(f"{relative}:{line_num}: {line.strip()}")
    return matches, disqualified, files_searched


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
    disqualifier: re.Pattern[str] | None = None
    if task.unaffected_shape:
        try:
            disqualifier = re.compile(task.unaffected_shape, re.IGNORECASE)
        except re.error:
            _LOGGER.warning("Invalid unaffected_shape regex for '%s': %s", task.title, task.unaffected_shape)

    matches, disqualified, files_searched = await hass.async_add_executor_job(
        _grep_files_sync,
        config_dir,
        ["*.yaml", "packages/**/*.yaml", "integrations/**/*.yaml", "mqtt/**/*.yaml"],
        [(".storage", "lovelace.*")],
        regex,
        disqualifier,
    )

    disqualified_note = (
        f" ({disqualified} well-formed occurrence(s) filtered out by unaffected_shape)" if disqualified else ""
    )

    if matches:
        match_text = "\n".join(f"  - {m}" for m in matches[:10])
        extra = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
        return CheckResult(
            check_id="grep_config",
            title=task.title,
            passed=False,
            detail=(
                f"Found '{pattern}' in {len(matches)} bug-shaped location(s) "
                f"across {files_searched} files{disqualified_note}:\n{match_text}{extra}"
                f"\n\n{task.if_found}"
            ),
            severity=task.severity,
        )

    return CheckResult(
        check_id="grep_config",
        title=task.title,
        passed=True,
        detail=(
            f"Searched {files_searched} YAML files — no bug-shaped matches for "
            f"'{pattern}'{disqualified_note}.\n\n{task.if_not_found}"
        ),
        severity=task.severity,
    )


def _get_entity_ids_for_integration(
    hass: HomeAssistant,
    integration: str,
    *,
    exclude_diagnostic: bool = False,
    domain: str = "",
) -> list[str]:
    """Get all entity IDs belonging to an integration using the entity registry."""
    ent_reg = er.async_get(hass)
    entity_ids: list[str] = []
    for entity in ent_reg.entities.values():
        if entity.platform == integration and not entity.disabled:
            if exclude_diagnostic and entity.entity_category is not None:
                continue
            if domain and entity.domain != domain:
                continue
            entity_ids.append(entity.entity_id)
    return entity_ids


def _count_diagnostic_unavailable(hass: HomeAssistant, integration: str) -> int:
    """Count unavailable diagnostic/config entities for an integration."""
    ent_reg = er.async_get(hass)
    count = 0
    for entity in ent_reg.entities.values():
        if entity.platform == integration and not entity.disabled and entity.entity_category is not None:
            state = hass.states.get(entity.entity_id)
            if state is None or state.state == "unavailable":
                count += 1
    return count


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

    entity_ids = _get_entity_ids_for_integration(hass, integration, exclude_diagnostic=True)
    total = len(entity_ids)
    unavailable_list: list[str] = []

    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is None or state.state == "unavailable":
            unavailable_list.append(eid)

    if total == 0:
        return CheckResult(
            check_id="entity_available",
            title=task.title,
            passed=True,
            detail=f"No entities found for '{integration}' in entity registry",
            severity=task.severity,
        )

    available = total - len(unavailable_list)

    if unavailable_list:
        sample = ", ".join(unavailable_list[:5])
        extra = f" and {len(unavailable_list) - 5} more" if len(unavailable_list) > 5 else ""
        return CheckResult(
            check_id="entity_available",
            title=task.title,
            passed=True,
            detail=(
                f"Baseline: {available}/{total} entities available for '{integration}'. "
                f"{len(unavailable_list)} currently unavailable (pre-existing, not upgrade-related): "
                f"{sample}{extra}"
            ),
            severity="info",
        )

    return CheckResult(
        check_id="entity_available",
        title=task.title,
        passed=True,
        detail=f"All {total} entities for '{integration}' are available",
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

    # Search automation YAML files — offloaded to executor for filesystem I/O
    config_dir = Path(hass.config.path())
    regex = re.compile(pattern, re.IGNORECASE)

    matches, _disqualified, _files_searched = await hass.async_add_executor_job(
        _grep_files_sync,
        config_dir,
        ["automations.yaml", "automations/*.yaml", "packages/**/*.yaml"],
        [],
        regex,
        None,
    )

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
    """Count unavailable entities, optionally filtered by integration.

    Excludes diagnostic/config entities (e.g. battery sensors on sleeping
    devices) from the main count to reduce noise.
    """
    integration = task.integration
    ent_reg = er.async_get(hass)

    # Build a set of diagnostic entity IDs for quick lookup
    diagnostic_ids: set[str] = set()
    for entity in ent_reg.entities.values():
        if (
            entity.entity_category is not None
            and not entity.disabled
            and (not integration or entity.platform == integration)
        ):
            diagnostic_ids.add(entity.entity_id)

    if integration:
        entity_ids = _get_entity_ids_for_integration(hass, integration, exclude_diagnostic=True)
        unavailable: list[str] = []
        for eid in entity_ids:
            state = hass.states.get(eid)
            if state is None or state.state == "unavailable":
                name = state.attributes.get("friendly_name", "") if state else ""
                unavailable.append(f"{eid} ({name})" if name else eid)
    else:
        unavailable = []
        for state in hass.states.async_all():
            if state.state == "unavailable" and state.entity_id not in diagnostic_ids:
                name = state.attributes.get("friendly_name", "")
                unavailable.append(f"{state.entity_id} ({name})" if name else state.entity_id)

    diag_unavailable = _count_diagnostic_unavailable(hass, integration) if integration else 0
    scope = f" for '{integration}'" if integration else ""
    diag_note = (
        f"\n  Note: {diag_unavailable} diagnostic entities also unavailable "
        f"(e.g. battery sensors on sleeping devices — expected)"
        if diag_unavailable
        else ""
    )

    if unavailable:
        sample = "\n".join(f"  - {e}" for e in unavailable[:10])
        extra = f"\n  ... and {len(unavailable) - 10} more" if len(unavailable) > 10 else ""
        return CheckResult(
            check_id="unavailable_entities",
            title=task.title,
            passed=True,
            detail=(
                f"Baseline: {len(unavailable)} entities currently unavailable{scope} "
                f"(pre-existing, not upgrade-related):\n{sample}{extra}{diag_note}"
            ),
            severity="info",
        )

    return CheckResult(
        check_id="unavailable_entities",
        title=task.title,
        passed=True,
        detail=f"No unavailable entities{scope}{diag_note}",
        severity="info",
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
    """Count entities for an integration using the entity registry."""
    integration = task.integration
    if not integration:
        return CheckResult(
            check_id="entity_count",
            title=task.title,
            passed=True,
            detail="No integration specified",
            severity=task.severity,
        )

    entity_ids = _get_entity_ids_for_integration(hass, integration, domain=task.domain)
    count = len(entity_ids)
    scope = f"'{integration}' {task.domain} entities" if task.domain else f"'{integration}'"

    if count > 0:
        sample_size = 10 if task.domain else 5
        sample = ", ".join(entity_ids[:sample_size])
        extra = f" and {count - sample_size} more" if count > sample_size else ""
        detail = f"Found {count} {scope}: {sample}{extra}"
    else:
        detail = f"No {scope} found"

    return CheckResult(
        check_id="entity_count",
        title=task.title,
        passed=True,
        detail=f"{detail}\n\n{task.if_found if count > 0 else task.if_not_found}",
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
