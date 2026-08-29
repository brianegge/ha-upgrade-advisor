"""Automated verification checks for upgrade breaking changes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import regex
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .sanitize import strip_markup

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
        # These strings are model-authored and get echoed back into the
        # phase-3 prompt, so an injected instruction would ride along with
        # the check results. Flatten any markup before storing them.
        tasks.append(
            CheckTask(
                check=item.get("check", "unknown"),
                title=strip_markup(item.get("title", f"Check {i + 1}")),
                severity=item.get("severity", "info"),
                context=strip_markup(item.get("context", "")),
                if_found=strip_markup(item.get("if_found", "")),
                if_not_found=strip_markup(item.get("if_not_found", "")),
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
_MAX_PATTERN_LENGTH = 200
_MAX_LINE_LENGTH = 1000
_MAX_MATCHES = 50
_MAX_MATCH_DISPLAY_LENGTH = 200
# Hard per-search execution bound (regex module timeout). The heuristic below
# rejects the obvious catastrophic shapes; this catches everything it misses.
_REGEX_TIMEOUT_SECONDS = 0.25

# A quantifier applied to a group that itself contains a quantifier — the
# classic catastrophic-backtracking shape (e.g. `(a+)+` or `(a{2,})+`).
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[*+{][^)]*\)\s*[*+{]")

# Narrow list, used to REJECT search patterns. Kept tight on purpose:
# over-rejecting here would block legitimate deprecation checks, since a
# lone `api_key:` search is a reasonable thing for a release note to want.
_SECRET_KEYWORDS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "api_key",
    "apikey",
    "secret",
    "credential",
    "bearer",
    "private_key",
)

# Broad lists, used only to REDACT values. Aggressive is correct here:
# masking one extra value costs nothing, missing one leaks a credential.
# Long names are matched anywhere in the key; short ones only as whole
# tokens, so `auth` does not fire on `author`.
_CREDENTIAL_KEY_SUBSTRINGS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "credential",
    "token",
    "apikey",
    "api_key",
    "private_key",
    "bearer",
    "signature",
    "session",
    "cookie",
    "webhook",
)
_CREDENTIAL_KEY_TOKENS = frozenset({"key", "keys", "psk", "pwd", "auth", "salt", "pin", "otp", "hash", "iv"})

_ASSIGNMENT = re.compile(r"^((?:-\s+)?[\"']?[\w.-]+[\"']?\s*[:=]\s*)(\S.*)$")
# A key with no value (`device_class:`) — often the bug shape itself.
_BARE_KEY = re.compile(r"^(?:-\s+)?[\"']?[\w.-]+[\"']?\s*[:=]\s*$")
# `key: value` pairs inside a line that is not a plain assignment, e.g. the
# flow-style `{token: abc, url: xyz}`.
_INLINE_ASSIGNMENT = re.compile(r"([\w.-]+[\"']?\s*[:=]\s*)([^,}\]\s]+)")

# A value is emitted verbatim only when it is recognizably harmless. These are
# deliberately narrow: the report needs the config *shape* to classify a bug,
# never the value, so anything unrecognized is masked regardless of the key.
_YAML_KEYWORDS = frozenset({"true", "false", "null", "none", "yes", "no", "on", "off"})
_NUMBER_OR_VERSION = re.compile(r"^-?\d+(?:\.\d+)*$")
_TEMPLATE = re.compile(r"^\{\{.*\}\}$")
_DOTTED_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+$")
# Letters only — a value mixing letters and digits (`hunter2`, `cGFzc3dvcmQ`)
# is not distinguishable from a credential by shape, so it is masked.
_PLAIN_WORD = re.compile(r"^[a-z][a-z_-]{0,19}$")
_HEXISH = re.compile(r"^[0-9a-f]{8,}$")


def _is_benign_value(value: str) -> bool:
    """True only for values that cannot plausibly be a credential."""
    value = value.strip().strip("\"'").strip()
    if not value:
        return True
    if value.lower() in _YAML_KEYWORDS:
        return True
    if _NUMBER_OR_VERSION.match(value) or _TEMPLATE.match(value):
        return True
    if _DOTTED_IDENTIFIER.match(value):
        return True
    return bool(_PLAIN_WORD.match(value)) and not _HEXISH.match(value)


# A pattern with no literal run of 3+ characters (`.`, `.*`, `^.*$`, `\S+`)
# matches everything, which turns a check into a wholesale config dump.
_LITERAL_RUN = re.compile(r"[A-Za-z0-9_]{3,}")

_SENSITIVE_FILENAME_PARTS = (
    "secret",
    "credential",
    "token",
    "password",
    "passwd",
    "known_devices",
    "key",
)


def validate_check_pattern(pattern: str) -> str | None:
    """Validate a model-supplied regex. Returns a rejection reason, or None if OK.

    The pattern originates from LLM output steered by third-party release
    notes, so it is untrusted: bound its size, refuse catastrophic-
    backtracking shapes, refuse patterns that hunt for credentials, and
    refuse patterns so broad they would harvest the config wholesale.
    """
    if len(pattern) > _MAX_PATTERN_LENGTH:
        return f"pattern exceeds {_MAX_PATTERN_LENGTH} characters"
    try:
        regex.compile(pattern)
    except regex.error as err:
        return f"invalid regex: {err}"
    if _NESTED_QUANTIFIER.search(pattern):
        return "nested quantifiers (catastrophic backtracking risk)"
    lowered = pattern.lower()
    if sum(1 for kw in _SECRET_KEYWORDS if kw in lowered) >= 2:
        return "matches multiple credential keywords — refusing to search for secrets"
    if not _LITERAL_RUN.search(pattern):
        return "pattern has no literal text — too broad to be a bug shape"
    return None


def _is_credential_key(key: str) -> bool:
    """True if the assignment key names something credential-like."""
    lowered = key.lower()
    if any(part in lowered for part in _CREDENTIAL_KEY_SUBSTRINGS):
        return True
    return any(token in _CREDENTIAL_KEY_TOKENS for token in re.split(r"[^a-z0-9]+", lowered))


def _redact_matched_line(line: str) -> str:
    """Mask the value of a matched config line before it is reported.

    Redaction is default-deny by value shape, not just by key name — a
    denylist of key names can never cover every integration's wording
    (`encryption_key`, `noise_psk`, `passphrase`, ...).
    """
    line = line.strip()[:_MAX_MATCH_DISPLAY_LENGTH]
    match = _ASSIGNMENT.match(line)
    if match is None:
        # A bare key carries no value, and is often the bug shape itself.
        if _BARE_KEY.match(line):
            return line
        # An unrecognized shape must not fall through unmasked — scrub every
        # `key: value` pair we can find inside it (flow-style YAML, quoted
        # keys with spaces) rather than trusting the line wholesale.
        if ":" in line or "=" in line:
            return _INLINE_ASSIGNMENT.sub(_redact_inline_pair, line)
        return line

    prefix, value = match.group(1), match.group(2).strip()
    # A `!secret` reference names a secret but does not contain one.
    if value.lower().startswith("!secret"):
        return line
    if _is_credential_key(prefix) or not _is_benign_value(value):
        return f"{prefix}<redacted>"
    return line


def _redact_inline_pair(match: re.Match[str]) -> str:
    """Mask one `key: value` pair found inside an unparsed line."""
    prefix, value = match.group(1), match.group(2)
    if value.lower().startswith("!secret"):
        return match.group(0)
    if _is_credential_key(prefix) or not _is_benign_value(value):
        return f"{prefix}<redacted>"
    return match.group(0)


def _is_sensitive_file(path: Path) -> bool:
    """Files that must never be grepped: secrets files and the .storage tree."""
    if any(part == ".storage" for part in path.parts):
        return True
    name = path.name.lower()
    return any(part in name for part in _SENSITIVE_FILENAME_PARTS)


def _grep_files_sync(
    config_dir: Path,
    glob_patterns: list[str],
    compiled: regex.Pattern,
    disqualifier: regex.Pattern | None,
    qualifier: regex.Pattern | None = None,
) -> tuple[list[str], int, int, int]:
    """Walk the config tree and grep matching lines. Runs in an executor.

    Skips secrets files and the .storage tree, bounds line length before
    matching, redacts credential values from matched lines, and stops after
    _MAX_MATCHES matches. Each search carries a hard timeout; TimeoutError
    propagates to the caller. When `qualifier` is set, a line that matches
    `pattern` is kept only if it also matches `qualifier` — used to scope
    entity-rename checks to entity IDs that actually belong to the
    integration being upgraded. Returns (matches, disqualified_count,
    out_of_scope_count, files_searched).
    """
    search_files: list[Path] = []
    for pattern in glob_patterns:
        search_files.extend(config_dir.glob(pattern))

    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in search_files:
        if f not in seen and not _is_sensitive_file(f):
            seen.add(f)
            unique_files.append(f)

    matches: list[str] = []
    disqualified = 0
    out_of_scope = 0
    files_searched = 0
    for search_file in unique_files:
        if len(matches) >= _MAX_MATCHES:
            break
        try:
            if search_file.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue
            content = search_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        files_searched += 1
        try:
            relative = search_file.relative_to(config_dir)
        except ValueError:
            relative = search_file
        for line_num, line in enumerate(content.split("\n"), 1):
            if len(matches) >= _MAX_MATCHES:
                break
            line = line[:_MAX_LINE_LENGTH]
            if not compiled.search(line, timeout=_REGEX_TIMEOUT_SECONDS):
                continue
            if disqualifier is not None and disqualifier.search(line, timeout=_REGEX_TIMEOUT_SECONDS):
                disqualified += 1
                continue
            if qualifier is not None and not qualifier.search(line, timeout=_REGEX_TIMEOUT_SECONDS):
                out_of_scope += 1
                continue
            matches.append(f"{relative}:{line_num}: {_redact_matched_line(line)}")
    return matches, disqualified, out_of_scope, files_searched


def _build_entity_scope(hass: HomeAssistant, integration: str, domain: str = "") -> regex.Pattern | None:
    """Compile a regex matching any entity ID registered to `integration`.

    Used to scope entity-targeting pattern checks (e.g. rename suffixes like
    `_status`) to the integration actually being upgraded, so entities from
    unrelated integrations that happen to share the naming shape are not
    flagged. Returns None when the integration has no registered entities.
    """
    entity_ids = _get_entity_ids_for_integration(hass, integration, domain=domain)
    if not entity_ids:
        return None
    return regex.compile("|".join(regex.escape(eid) for eid in entity_ids), regex.IGNORECASE)


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

    rejection = validate_check_pattern(pattern)
    if rejection:
        _LOGGER.warning("Rejected grep pattern for '%s': %s (%s)", task.title, pattern, rejection)
        return CheckResult(
            check_id="grep_config",
            title=task.title,
            passed=True,
            detail=f"Check skipped — unsafe search pattern rejected ({rejection})",
            severity="info",
        )

    compiled = regex.compile(pattern, regex.IGNORECASE)
    disqualifier: regex.Pattern | None = None
    if task.unaffected_shape:
        shape_rejection = validate_check_pattern(task.unaffected_shape)
        if shape_rejection is None:
            disqualifier = regex.compile(task.unaffected_shape, regex.IGNORECASE)
        else:
            _LOGGER.warning(
                "Rejected unaffected_shape regex for '%s': %s (%s)",
                task.title,
                task.unaffected_shape,
                shape_rejection,
            )

    qualifier: regex.Pattern | None = None
    if task.integration:
        qualifier = _build_entity_scope(hass, task.integration, task.domain)
        if qualifier is None:
            return CheckResult(
                check_id="grep_config",
                title=task.title,
                passed=True,
                detail=(
                    f"No entities registered to '{task.integration}'"
                    f"{f' in domain {task.domain}' if task.domain else ''} — nothing to check."
                ),
                severity=task.severity,
            )

    try:
        matches, disqualified, out_of_scope, files_searched = await hass.async_add_executor_job(
            _grep_files_sync,
            config_dir,
            ["*.yaml", "packages/**/*.yaml", "integrations/**/*.yaml", "mqtt/**/*.yaml"],
            compiled,
            disqualifier,
            qualifier,
        )
    except TimeoutError:
        _LOGGER.warning("Grep pattern for '%s' timed out: %s", task.title, pattern)
        return CheckResult(
            check_id="grep_config",
            title=task.title,
            passed=True,
            detail="Check skipped — search pattern timed out (catastrophic backtracking)",
            severity="info",
        )

    disqualified_note = (
        f" ({disqualified} well-formed occurrence(s) filtered out by unaffected_shape)" if disqualified else ""
    )
    scope_note = (
        f" ({out_of_scope} match(es) not referencing any '{task.integration}' entity discarded as unrelated)"
        if out_of_scope
        else ""
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
                f"across {files_searched} files{disqualified_note}{scope_note}:\n{match_text}{extra}"
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
            f"'{pattern}'{disqualified_note}{scope_note}.\n\n{task.if_not_found}"
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

    rejection = validate_check_pattern(pattern)
    if rejection:
        _LOGGER.warning("Rejected automation pattern for '%s': %s (%s)", task.title, pattern, rejection)
        return CheckResult(
            check_id="automation_references",
            title=task.title,
            passed=True,
            detail=f"Check skipped — unsafe search pattern rejected ({rejection})",
            severity="info",
        )

    # Search automation YAML files — offloaded to executor for filesystem I/O
    config_dir = Path(hass.config.path())
    compiled = regex.compile(pattern, regex.IGNORECASE)

    qualifier: regex.Pattern | None = None
    if task.integration:
        qualifier = _build_entity_scope(hass, task.integration, task.domain)
        if qualifier is None:
            return CheckResult(
                check_id="automation_references",
                title=task.title,
                passed=True,
                detail=(
                    f"No entities registered to '{task.integration}'"
                    f"{f' in domain {task.domain}' if task.domain else ''} — nothing to check."
                ),
                severity=task.severity,
            )

    try:
        matches, _disqualified, out_of_scope, _files_searched = await hass.async_add_executor_job(
            _grep_files_sync,
            config_dir,
            ["automations.yaml", "automations/*.yaml", "packages/**/*.yaml"],
            compiled,
            None,
            qualifier,
        )

        # Also check automation entity states for friendly names matching pattern
        auto_states = hass.states.async_all("automation")
        entity_matches: list[str] = []
        for state in auto_states:
            friendly = state.attributes.get("friendly_name", "")
            if compiled.search(friendly, timeout=_REGEX_TIMEOUT_SECONDS) or compiled.search(
                state.entity_id, timeout=_REGEX_TIMEOUT_SECONDS
            ):
                if qualifier is not None and not (
                    qualifier.search(friendly, timeout=_REGEX_TIMEOUT_SECONDS)
                    or qualifier.search(state.entity_id, timeout=_REGEX_TIMEOUT_SECONDS)
                ):
                    out_of_scope += 1
                    continue
                entity_matches.append(friendly or state.entity_id)
    except TimeoutError:
        _LOGGER.warning("Automation pattern for '%s' timed out: %s", task.title, pattern)
        return CheckResult(
            check_id="automation_references",
            title=task.title,
            passed=True,
            detail="Check skipped — search pattern timed out (catastrophic backtracking)",
            severity="info",
        )

    all_matches = matches + [f"automation: {m}" for m in entity_matches]
    scope_note = (
        f" ({out_of_scope} match(es) not referencing any '{task.integration}' entity discarded as unrelated)"
        if out_of_scope
        else ""
    )

    if all_matches:
        match_text = "\n".join(f"  - {m}" for m in all_matches[:10])
        return CheckResult(
            check_id="automation_references",
            title=task.title,
            passed=False,
            detail=(
                f"Found {len(all_matches)} reference(s) to '{pattern}'{scope_note}:\n{match_text}\n\n{task.if_found}"
            ),
            severity=task.severity,
        )

    return CheckResult(
        check_id="automation_references",
        title=task.title,
        passed=True,
        detail=f"No references to '{pattern}' found in automations{scope_note}.\n\n{task.if_not_found}",
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
