"""Tests for the checker module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.upgrade_advisor.checker import (
    CheckResult,
    CheckTask,
    _check_entity_available,
    _check_entity_count,
    _check_grep_config,
    _check_unavailable_entities,
    _count_diagnostic_unavailable,
    _get_entity_ids_for_integration,
    check_result_from_dict,
    check_result_to_dict,
    check_task_from_dict,
    check_task_to_dict,
    parse_check_tasks,
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
