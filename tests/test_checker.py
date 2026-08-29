"""Tests for the checker module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor.checker import (
    CheckResult,
    CheckTask,
    _check_automation_references,
    _check_entity_available,
    _check_entity_count,
    _check_grep_config,
    _check_unavailable_entities,
    _count_diagnostic_unavailable,
    _get_entity_ids_for_integration,
    _redact_matched_line,
    check_result_from_dict,
    check_result_to_dict,
    check_task_from_dict,
    check_task_to_dict,
    parse_check_tasks,
    validate_check_pattern,
)


def _make_entity(entity_id: str, platform: str, disabled: bool = False, entity_category: str | None = None):
    """Create a mock entity registry entry."""
    entry = MagicMock()
    entry.entity_id = entity_id
    entry.platform = platform
    entry.disabled = disabled
    entry.entity_category = entity_category
    entry.domain = entity_id.split(".")[0]
    return entry


def _mock_entity_registry(entities: list):
    """Create a mock entity registry with the given entities."""
    reg = MagicMock()
    reg.entities = MagicMock()
    reg.entities.values.return_value = entities
    return reg


# --- parse_check_tasks ---


def test_parse_check_tasks_valid_json() -> None:
    """Test parsing valid JSON check tasks."""
    raw = '[{"check": "backup_recent", "title": "Check backup"}]'
    tasks = parse_check_tasks(raw)
    assert len(tasks) == 1
    assert tasks[0].check == "backup_recent"
    assert tasks[0].title == "Check backup"


def test_parse_check_tasks_markdown_wrapped() -> None:
    """Test parsing JSON wrapped in markdown code block."""
    raw = '```json\n[{"check": "grep_config", "title": "Find deprecated", "pattern": "old_key"}]\n```'
    tasks = parse_check_tasks(raw)
    assert len(tasks) == 1
    assert tasks[0].pattern == "old_key"


def test_parse_check_tasks_no_json() -> None:
    """Test parsing with no JSON array."""
    tasks = parse_check_tasks("No JSON here")
    assert tasks == []


def test_parse_check_tasks_invalid_json() -> None:
    """Test parsing with invalid JSON."""
    tasks = parse_check_tasks("[{invalid json}]")
    assert tasks == []


# --- _get_entity_ids_for_integration ---


def test_get_entity_ids_includes_all(hass: HomeAssistant) -> None:
    """Test getting entity IDs includes all non-disabled entities."""
    entities = [
        _make_entity("sensor.temp", "climate_integration"),
        _make_entity("sensor.battery", "climate_integration", entity_category="diagnostic"),
        _make_entity("sensor.disabled", "climate_integration", disabled=True),
        _make_entity("sensor.other", "other_integration"),
    ]
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = _get_entity_ids_for_integration(hass, "climate_integration")
    assert result == ["sensor.temp", "sensor.battery"]


def test_get_entity_ids_exclude_diagnostic(hass: HomeAssistant) -> None:
    """Test getting entity IDs excludes diagnostic entities."""
    entities = [
        _make_entity("sensor.temp", "zwave", entity_category=None),
        _make_entity("sensor.battery", "zwave", entity_category="diagnostic"),
        _make_entity("sensor.config_val", "zwave", entity_category="config"),
    ]
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = _get_entity_ids_for_integration(hass, "zwave", exclude_diagnostic=True)
    assert result == ["sensor.temp"]


def test_get_entity_ids_empty(hass: HomeAssistant) -> None:
    """Test getting entity IDs for integration with no entities."""
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry([])):
        result = _get_entity_ids_for_integration(hass, "nonexistent")
    assert result == []


# --- _count_diagnostic_unavailable ---


def test_count_diagnostic_unavailable(hass: HomeAssistant) -> None:
    """Test counting unavailable diagnostic entities."""
    entities = [
        _make_entity("sensor.battery_low", "zwave", entity_category="diagnostic"),
        _make_entity("sensor.battery_ok", "zwave", entity_category="diagnostic"),
        _make_entity("sensor.temp", "zwave", entity_category=None),
    ]
    hass.states.async_set("sensor.battery_low", "unavailable")
    hass.states.async_set("sensor.battery_ok", "on")
    hass.states.async_set("sensor.temp", "unavailable")

    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        count = _count_diagnostic_unavailable(hass, "zwave")
    assert count == 1


def test_count_diagnostic_unavailable_none_missing(hass: HomeAssistant) -> None:
    """Test counting when no diagnostic entities are unavailable."""
    entities = [
        _make_entity("sensor.battery", "zwave", entity_category="diagnostic"),
    ]
    hass.states.async_set("sensor.battery", "on")

    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        count = _count_diagnostic_unavailable(hass, "zwave")
    assert count == 0


# --- _check_entity_available ---


@pytest.mark.parametrize(
    ("states", "expected_detail_contains"),
    [
        ({"sensor.temp": "20", "sensor.humidity": "50"}, "All 2"),
        ({"sensor.temp": "unavailable", "sensor.humidity": "50"}, "Baseline"),
    ],
)
async def test_check_entity_available(hass: HomeAssistant, states: dict, expected_detail_contains: str) -> None:
    """Test entity available check reports baseline, always passes."""
    entities = [
        _make_entity("sensor.temp", "hue", entity_category=None),
        _make_entity("sensor.humidity", "hue", entity_category=None),
    ]
    for eid, state in states.items():
        hass.states.async_set(eid, state)

    task = CheckTask(check="entity_available", title="Hue entities", integration="hue")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_entity_available(hass, task)

    assert result.passed is True
    assert expected_detail_contains in result.detail


async def test_check_entity_available_no_integration(hass: HomeAssistant) -> None:
    """Test entity available check with no integration specified."""
    task = CheckTask(check="entity_available", title="Test", integration="")
    result = await _check_entity_available(hass, task)
    assert result.passed is True
    assert "No integration specified" in result.detail


async def test_check_entity_available_no_entities(hass: HomeAssistant) -> None:
    """Test entity available check with no entities found."""
    task = CheckTask(check="entity_available", title="Test", integration="nonexistent")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry([])):
        result = await _check_entity_available(hass, task)
    assert result.passed is True
    assert "No entities found" in result.detail


async def test_check_entity_available_unavailable_is_info(hass: HomeAssistant) -> None:
    """Test that unavailable entities are reported as info severity."""
    entities = [_make_entity("sensor.temp", "hue", entity_category=None)]
    hass.states.async_set("sensor.temp", "unavailable")

    task = CheckTask(check="entity_available", title="Hue", integration="hue", severity="breaking")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_entity_available(hass, task)

    assert result.passed is True
    assert result.severity == "info"


# --- _check_unavailable_entities ---


async def test_check_unavailable_entities_with_integration(hass: HomeAssistant) -> None:
    """Test unavailable entities check filtered by integration."""
    entities = [
        _make_entity("sensor.temp", "mqtt", entity_category=None),
        _make_entity("sensor.battery", "mqtt", entity_category="diagnostic"),
    ]
    hass.states.async_set("sensor.temp", "unavailable")
    hass.states.async_set("sensor.battery", "unavailable")

    task = CheckTask(check="unavailable_entities", title="MQTT check", integration="mqtt")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_unavailable_entities(hass, task)

    assert result.passed is True
    assert result.severity == "info"
    assert "Baseline" in result.detail
    assert "sensor.temp" in result.detail
    assert "diagnostic" in result.detail


async def test_check_unavailable_entities_none_unavailable(hass: HomeAssistant) -> None:
    """Test unavailable entities check when all are available."""
    entities = [_make_entity("sensor.temp", "mqtt", entity_category=None)]
    hass.states.async_set("sensor.temp", "20")

    task = CheckTask(check="unavailable_entities", title="MQTT check", integration="mqtt")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_unavailable_entities(hass, task)

    assert result.passed is True
    assert "No unavailable" in result.detail


def test_get_entity_ids_domain_filter(hass: HomeAssistant) -> None:
    """Test filtering entities by entity domain within an integration."""
    entities = [
        _make_entity("sensor.temp", "esphome"),
        _make_entity("binary_sensor.motion", "esphome"),
        _make_entity("light.lamp", "esphome"),
    ]
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = _get_entity_ids_for_integration(hass, "esphome", domain="light")
    assert result == ["light.lamp"]


def test_check_task_roundtrips_through_dict() -> None:
    """CheckTask survives a to_dict / from_dict roundtrip, ignoring unknown fields."""
    task = CheckTask(
        check="entity_count",
        title="ESPHome lights",
        severity="post_upgrade",
        context="PR 12345",
        pattern="color_temp",
        integration="esphome",
        domain="light",
    )
    raw = check_task_to_dict(task)
    raw["unknown_new_field"] = "ignored"
    restored = check_task_from_dict(raw)
    assert restored == task


def test_check_result_roundtrips_through_dict() -> None:
    """CheckResult survives a to_dict / from_dict roundtrip."""
    result = CheckResult(
        check_id="entity_count",
        title="ESPHome lights",
        passed=False,
        detail="Found 3 entities",
        severity="post_upgrade",
    )
    assert check_result_from_dict(check_result_to_dict(result)) == result


def test_parse_check_tasks_includes_domain() -> None:
    """Test that parse_check_tasks picks up the `domain` field."""
    raw = '[{"check": "entity_count", "title": "ESPHome lights", "integration": "esphome", "domain": "light"}]'
    tasks = parse_check_tasks(raw)
    assert len(tasks) == 1
    assert tasks[0].integration == "esphome"
    assert tasks[0].domain == "light"


def test_parse_check_tasks_includes_unaffected_shape() -> None:
    """Test that parse_check_tasks picks up `unaffected_shape`."""
    raw = (
        '[{"check": "grep_config", "title": "template device_class",'
        ' "pattern": "device_class:", "unaffected_shape": "device_class:\\\\s*\\\\w+"}]'
    )
    tasks = parse_check_tasks(raw)
    assert len(tasks) == 1
    assert tasks[0].unaffected_shape == r"device_class:\s*\w+"


async def test_check_grep_config_unaffected_shape_filters_benign(hass: HomeAssistant, tmp_path) -> None:
    """Lines matching unaffected_shape should be filtered out as benign."""
    config_file = tmp_path / "configuration.yaml"
    config_file.write_text(
        "binary_sensor:\n"
        "  - platform: template\n"
        "    sensors:\n"
        "      window_a:\n"
        "        device_class: window\n"  # well-formed — should be filtered
        "      window_b:\n"
        "        device_class: occupancy\n"  # well-formed — should be filtered
        "      window_c:\n"
        "        device_class:\n"  # empty — the bug shape
    )
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(
        check="grep_config",
        title="Template device_class stripped",
        pattern=r"device_class:",
        unaffected_shape=r"device_class:\s*\w+",
        severity="breaking",
        if_found="Bug-shaped device_class definitions may be stripped.",
    )
    result = await _check_grep_config(hass, task)

    assert result.passed is False
    assert "1 bug-shaped location" in result.detail
    assert "2 well-formed occurrence(s) filtered out" in result.detail
    assert "window_c" not in result.detail  # the key isn't on the matching line
    assert "device_class:" in result.detail


async def test_check_grep_config_no_matches_still_reports_disqualified(hass: HomeAssistant, tmp_path) -> None:
    """When every match is benign, the check passes but reports how many were filtered."""
    config_file = tmp_path / "configuration.yaml"
    config_file.write_text(
        "template:\n"
        "  - binary_sensor:\n"
        "      - name: Front\n"
        "        device_class: door\n"
        "      - name: Back\n"
        "        device_class: window\n"
    )
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(
        check="grep_config",
        title="Template device_class stripped",
        pattern=r"device_class:",
        unaffected_shape=r"device_class:\s*\w+",
        severity="breaking",
        if_not_found="No bug-shaped definitions found.",
    )
    result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "2 well-formed occurrence(s) filtered out" in result.detail
    assert "No bug-shaped definitions found." in result.detail


async def test_check_grep_config_entity_scope_filters_unrelated(hass: HomeAssistant, tmp_path) -> None:
    """With `integration` set, matches not referencing that integration's entities are discarded."""
    config_file = tmp_path / "configuration.yaml"
    config_file.write_text(
        "sensor:\n"
        "  - platform: template\n"
        "    sensors:\n"
        "      rufus:\n"
        "        value_template: \"{{ states('sensor.rufus_status') }}\"\n"  # unrelated integration
        "      fuel:\n"
        "        value_template: \"{{ states('sensor.fordpass_fuel_status') }}\"\n"  # in scope
    )
    hass.config.config_dir = str(tmp_path)

    entities = [
        _make_entity("sensor.fordpass_fuel_status", "fordpass"),
        _make_entity("sensor.rufus_status", "other_integration"),
    ]
    task = CheckTask(
        check="grep_config",
        title="FordPass entity rename",
        pattern=r"_status\b",
        integration="fordpass",
        severity="breaking",
        if_found="These reference renamed FordPass entities.",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_grep_config(hass, task)

    assert result.passed is False
    assert "1 bug-shaped location" in result.detail
    assert "sensor.fordpass_fuel_status" in result.detail
    assert "sensor.rufus_status" not in result.detail
    assert "1 match(es) not referencing any 'fordpass' entity discarded" in result.detail


async def test_check_grep_config_entity_scope_all_unrelated_passes(hass: HomeAssistant, tmp_path) -> None:
    """When every match belongs to other integrations, the scoped check passes."""
    config_file = tmp_path / "configuration.yaml"
    config_file.write_text(
        "rufus: \"{{ states('sensor.rufus_status') }}\"\ncamera_watch: sensor.ai_camera_detector_status\n",
    )
    hass.config.config_dir = str(tmp_path)

    entities = [_make_entity("sensor.fordpass_fuel_status", "fordpass")]
    task = CheckTask(
        check="grep_config",
        title="FordPass entity rename",
        pattern=r"_status\b",
        integration="fordpass",
        severity="breaking",
        if_not_found="No config references FordPass entities being renamed.",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "2 match(es) not referencing any 'fordpass' entity discarded" in result.detail
    assert "No config references FordPass entities being renamed." in result.detail


async def test_check_grep_config_entity_scope_no_entities(hass: HomeAssistant, tmp_path) -> None:
    """A scoped check for an integration with no registered entities passes with no search."""
    (tmp_path / "configuration.yaml").write_text("anything: sensor.foo_status\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(
        check="grep_config",
        title="FordPass entity rename",
        pattern=r"_status\b",
        integration="fordpass",
        severity="breaking",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry([])):
        result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "No entities registered to 'fordpass'" in result.detail


async def test_check_automation_references_entity_scope(hass: HomeAssistant, tmp_path) -> None:
    """Automation checks scoped to an integration ignore lookalike entities."""
    automations_file = tmp_path / "automations.yaml"
    automations_file.write_text(
        "- alias: phone battery\n"
        "  entity_id: sensor.brians_iphone_12_battery_state\n"  # unrelated integration
        "- alias: car charging\n"
        "  entity_id: sensor.fordpass_charge_state\n"  # in scope
    )
    hass.config.config_dir = str(tmp_path)

    entities = [
        _make_entity("sensor.fordpass_charge_state", "fordpass"),
        _make_entity("sensor.brians_iphone_12_battery_state", "mobile_app"),
    ]
    task = CheckTask(
        check="automation_references",
        title="Automations using renamed FordPass entities",
        pattern=r"_state\b",
        integration="fordpass",
        severity="breaking",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_automation_references(hass, task)

    assert result.passed is False
    assert "sensor.fordpass_charge_state" in result.detail
    assert "iphone" not in result.detail
    assert "1 match(es) not referencing any 'fordpass' entity discarded" in result.detail


async def test_check_automation_references_entity_scope_no_entities(hass: HomeAssistant, tmp_path) -> None:
    """A scoped automation check for an entity-less integration passes with no search."""
    (tmp_path / "automations.yaml").write_text("- entity_id: sensor.foo_state\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(
        check="automation_references",
        title="Automations using renamed FordPass entities",
        pattern=r"_state\b",
        integration="fordpass",
        severity="breaking",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry([])):
        result = await _check_automation_references(hass, task)

    assert result.passed is True
    assert "No entities registered to 'fordpass'" in result.detail


async def test_check_entity_count_with_domain_match(hass: HomeAssistant) -> None:
    """entity_count with a domain filter counts only entities in that domain."""
    entities = [
        _make_entity("sensor.temp", "esphome"),
        _make_entity("binary_sensor.motion", "esphome"),
        _make_entity("light.lamp", "esphome"),
    ]
    task = CheckTask(check="entity_count", title="ESPHome lights", integration="esphome", domain="light")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_entity_count(hass, task)

    assert result.passed is True
    assert "Found 1" in result.detail
    assert "light.lamp" in result.detail
    assert "'esphome' light" in result.detail


async def test_check_entity_count_with_domain_no_match(hass: HomeAssistant) -> None:
    """entity_count with a domain filter reports zero when no matches."""
    entities = [
        _make_entity("sensor.temp", "esphome"),
        _make_entity("binary_sensor.motion", "esphome"),
    ]
    task = CheckTask(
        check="entity_count",
        title="ESPHome lights",
        integration="esphome",
        domain="light",
        if_not_found="Skip — no ESPHome lights.",
    )
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_entity_count(hass, task)

    assert result.passed is True
    assert "No 'esphome' light entities found" in result.detail
    assert "Skip — no ESPHome lights." in result.detail


# --- pattern validation / grep hardening ---


def test_validate_pattern_accepts_normal_patterns() -> None:
    """Ordinary bug-shape patterns are accepted."""
    assert validate_check_pattern(r"device_class:\s*$") is None
    assert validate_check_pattern(r"platform:\s*template") is None
    assert validate_check_pattern(r"api_key:") is None  # single credential keyword is fine


def test_validate_pattern_rejects_too_long() -> None:
    """Patterns over the length cap are rejected."""
    assert validate_check_pattern("a" * 201) is not None


def test_validate_pattern_rejects_invalid_regex() -> None:
    """Uncompilable regexes are rejected with a reason."""
    reason = validate_check_pattern("[unclosed")
    assert reason is not None
    assert "invalid regex" in reason


def test_validate_pattern_rejects_nested_quantifiers() -> None:
    """Catastrophic-backtracking shapes are rejected."""
    assert validate_check_pattern(r"(a+)+b") is not None
    assert validate_check_pattern(r"(\w*)*x") is not None
    assert validate_check_pattern(r"(a{2,})+b") is not None
    assert validate_check_pattern(r"(?:(?:a+)+)b") is not None


def test_validate_pattern_rejects_secret_hunting() -> None:
    """Broad credential-keyword alternations are rejected."""
    assert validate_check_pattern(r"password|token|api_key") is not None
    assert validate_check_pattern(r"(secret|credential)") is not None


def test_redact_matched_line_masks_credential_values() -> None:
    """Values assigned to credential-like keys are masked."""
    assert _redact_matched_line("api_key: changeme") == "api_key: <redacted>"
    assert _redact_matched_line("  my_password: changeme") == "my_password: <redacted>"
    assert _redact_matched_line("token=changeme") == "token=<redacted>"


def test_redact_matched_line_masks_quoted_keys() -> None:
    """Quoted YAML/JSON credential keys are also redacted."""
    assert _redact_matched_line('"password": "changeme"') == '"password": <redacted>'
    assert _redact_matched_line("'api_key': 'changeme'") == "'api_key': <redacted>"


def test_redact_matched_line_keeps_secret_references() -> None:
    """!secret references are indirections, not values — left visible."""
    assert _redact_matched_line("api_key: !secret my_api_key") == "api_key: !secret my_api_key"


def test_redact_matched_line_leaves_ordinary_lines() -> None:
    """Lines without credential keys are unchanged (beyond trim/truncate)."""
    assert _redact_matched_line("  device_class: window  ") == "device_class: window"


@pytest.mark.parametrize(
    "line",
    [
        'encryption_key: "abc123=="',
        'noise_psk: "deadbeef"',
        "passphrase: hunter2",
        "key: sk-live-XYZ",
        "auth: Basic QWxhZGRpbg==",
        "- password: hunter2",
        'client_secret: "s3cr3t"',
        "webhook_id: aVeryLongOpaqueIdentifier123456",
        # Non-credential keys whose values are still secret-shaped: a value
        # mixing letters and digits, base64, or long hex is indistinguishable
        # from a credential, so it is masked regardless of the key name.
        "client: hunter2",
        "mqtt: cGFzc3dvcmQ",
        "value: deadbeefcafe",
    ],
)
def test_redact_matched_line_masks_non_keyword_secrets(line: str) -> None:
    """Secrets whose key names fall outside the credential list are still masked.

    A key-name denylist can never cover every integration's wording, so
    redaction is default-deny by value shape as well.
    """
    redacted = _redact_matched_line(line)
    assert "<redacted>" in redacted
    value = line.split(":", 1)[1] if ":" in line else line
    assert value.strip().strip("\"'") not in redacted


@pytest.mark.parametrize(
    "line",
    [
        "device_class: window",
        "platform: template",
        "- platform: mqtt",
        "device_class:",
        "api_key: !secret my_api_key",
        'value_template: "{{ states(1) }}"',
        "port: 8123",
        "author: frenck",
        "enabled: true",
    ],
)
def test_redact_matched_line_keeps_benign_evidence(line: str) -> None:
    """Benign config shapes stay readable — the report quotes them as evidence."""
    assert _redact_matched_line(line) == line.strip()


def test_redact_matched_line_scrubs_flow_style_yaml() -> None:
    """A line that isn't a plain assignment must not fall through unmasked."""
    redacted = _redact_matched_line("{token: abc123xyz, platform: mqtt}")
    assert "abc123xyz" not in redacted
    assert "<redacted>" in redacted
    # The benign pair in the same line is still readable.
    assert "platform: mqtt" in redacted


def test_redact_matched_line_scrubs_quoted_key_with_space() -> None:
    """Quoted keys containing spaces don't parse as assignments — still masked."""
    redacted = _redact_matched_line('"my key": s3cr3tvalue')
    assert "s3cr3tvalue" not in redacted


def test_redact_matched_line_keeps_non_assignment_text() -> None:
    """Lines with no assignment at all are plain text and stay readable."""
    assert _redact_matched_line("  - some_list_item") == "- some_list_item"


@pytest.mark.parametrize("pattern", [".", ".*", "^.*$", r"\S+", r"[\s\S]*"])
def test_validate_pattern_rejects_overly_broad(pattern: str) -> None:
    """Patterns with no literal text would harvest the config wholesale."""
    reason = validate_check_pattern(pattern)
    assert reason is not None
    assert "too broad" in reason


@pytest.mark.parametrize("pattern", ["device_class:", r"platform:\s*template", "ok_line", "key:"])
def test_validate_pattern_allows_real_bug_shapes(pattern: str) -> None:
    """Patterns anchored on a real config key are still accepted."""
    assert validate_check_pattern(pattern) is None


def test_parse_check_tasks_strips_markup_from_model_text() -> None:
    """Task text is echoed into the phase-3 prompt, so markup is flattened."""
    raw = (
        '[{"check": "grep_config", "title": "Check [x](https://attacker.example/a)",'
        ' "context": "![](https://attacker.example/i)",'
        ' "if_found": "Visit https://attacker.example/x", "pattern": "foo_bar"}]'
    )
    tasks = parse_check_tasks(raw)
    assert len(tasks) == 1
    assert tasks[0].title == "Check x"
    assert "attacker.example" not in tasks[0].context
    assert "`https://attacker.example/x`" in tasks[0].if_found


def test_is_sensitive_file() -> None:
    """The exclusion guard covers .storage, secrets variants, but not config files."""
    from pathlib import Path

    from custom_components.upgrade_advisor.checker import _is_sensitive_file

    assert _is_sensitive_file(Path("/config/.storage/lovelace.dashboard"))
    assert _is_sensitive_file(Path("/config/.storage/anything"))
    assert _is_sensitive_file(Path("/config/secrets.yaml"))
    assert _is_sensitive_file(Path("/config/prod_secrets.yaml"))
    assert _is_sensitive_file(Path("/config/packages/homeassistant_secrets.yml"))
    assert _is_sensitive_file(Path("/config/credentials.yaml"))
    assert _is_sensitive_file(Path("/config/tokens.yaml"))
    assert _is_sensitive_file(Path("/config/passwords.yaml"))
    assert _is_sensitive_file(Path("/config/api_keys.yaml"))
    assert _is_sensitive_file(Path("/config/known_devices.yaml"))
    assert not _is_sensitive_file(Path("/config/configuration.yaml"))
    assert not _is_sensitive_file(Path("/config/automations.yaml"))


async def test_check_grep_config_skips_secrets_and_storage(hass: HomeAssistant, tmp_path) -> None:
    """secrets.yaml and .storage are never searched."""
    (tmp_path / "configuration.yaml").write_text("ok_line: value\n")
    (tmp_path / "secrets.yaml").write_text("ok_line_wifi_password: changeme\n")
    storage = tmp_path / ".storage"
    storage.mkdir()
    (storage / "lovelace.dashboard").write_text('{"ok_line": true}\n')
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="grep_config", title="Skip sensitive", pattern="ok_line")
    result = await _check_grep_config(hass, task)

    assert result.passed is False
    assert "configuration.yaml" in result.detail
    assert "secrets.yaml" not in result.detail
    assert ".storage" not in result.detail
    assert "1 bug-shaped location" in result.detail


async def test_check_grep_config_rejects_unsafe_pattern(hass: HomeAssistant, tmp_path) -> None:
    """A secret-hunting pattern is rejected and the check skipped."""
    (tmp_path / "configuration.yaml").write_text("password: changeme\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="grep_config", title="Evil", pattern="password|token|api_key")
    result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "unsafe search pattern rejected" in result.detail
    assert "changeme" not in result.detail


async def test_check_grep_config_rejects_invalid_pattern_gracefully(hass: HomeAssistant, tmp_path) -> None:
    """An uncompilable pattern skips the check instead of raising."""
    (tmp_path / "configuration.yaml").write_text("key: value\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="grep_config", title="Broken", pattern="[unclosed")
    result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "unsafe search pattern rejected" in result.detail


async def test_check_grep_config_redacts_matched_secrets(hass: HomeAssistant, tmp_path) -> None:
    """Credential values on matched lines are redacted in the report."""
    (tmp_path / "configuration.yaml").write_text("api_key: example_value\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="grep_config", title="Deprecated api_key", pattern="api_key:")
    result = await _check_grep_config(hass, task)

    assert result.passed is False
    assert "example_value" not in result.detail
    assert "<redacted>" in result.detail


async def test_check_grep_config_timeout_skips_gracefully(hass: HomeAssistant, tmp_path) -> None:
    """A pattern that blows the regex execution timeout skips the check."""
    (tmp_path / "configuration.yaml").write_text("key: value\n")
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="grep_config", title="Slow", pattern="key:")
    with patch("custom_components.upgrade_advisor.checker._grep_files_sync", side_effect=TimeoutError):
        result = await _check_grep_config(hass, task)

    assert result.passed is True
    assert "timed out" in result.detail


async def test_check_automation_references_rejects_unsafe_pattern(hass: HomeAssistant, tmp_path) -> None:
    """automation_references applies the same pattern validation."""
    hass.config.config_dir = str(tmp_path)

    task = CheckTask(check="automation_references", title="Evil", pattern="(a+)+b")
    result = await _check_automation_references(hass, task)

    assert result.passed is True
    assert "unsafe search pattern rejected" in result.detail


async def test_check_unavailable_entities_global(hass: HomeAssistant) -> None:
    """Test unavailable entities check without integration filter."""
    entities = [
        _make_entity("sensor.temp", "mqtt", entity_category=None),
        _make_entity("sensor.diag", "mqtt", entity_category="diagnostic"),
    ]
    hass.states.async_set("sensor.temp", "unavailable")
    hass.states.async_set("sensor.diag", "unavailable")

    task = CheckTask(check="unavailable_entities", title="Global check", integration="")
    with patch("custom_components.upgrade_advisor.checker.er.async_get", return_value=_mock_entity_registry(entities)):
        result = await _check_unavailable_entities(hass, task)

    assert result.passed is True
    # Global check should exclude diagnostic entities
    assert "sensor.temp" in result.detail
    assert "sensor.diag" not in result.detail
