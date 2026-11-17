"""Tests for Marketplace (formerly HACS) support and entity name lookup."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor import UpgradeAdvisorCoordinator, _is_component_update
from custom_components.upgrade_advisor.const import (
    CONF_SCAN_HACS,
    DOMAIN,
    HA_CORE_UPDATE_ENTITY,
    MARKETPLACE_UPGRADE_TYPE,
)
from custom_components.upgrade_advisor.summarize import async_get_automation_summaries

from .conftest import MOCK_CONFIG

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / DOMAIN
GITHUB_RELEASE = "https://github.com/thomasloven/lovelace-card-mod/releases/tag/v4.0.0"


def _coordinator(hass: HomeAssistant, **options: object) -> UpgradeAdvisorCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG.copy(), options=options, unique_id=DOMAIN)
    return UpgradeAdvisorCoordinator(hass, entry)


def _register(hass: HomeAssistant, platform: str, object_id: str) -> str:
    entry = er.async_get(hass).async_get_or_create("update", platform, object_id, suggested_object_id=object_id)
    return entry.entity_id


# --- Option label rename keeps the stored key ---


def test_stored_option_key_unchanged() -> None:
    """Existing config entries store their choice under scan_hacs_updates."""
    assert CONF_SCAN_HACS == "scan_hacs_updates"


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json"])
def test_option_label_says_marketplace(path: str) -> None:
    """The options flow labels the stored key with Marketplace wording."""
    strings = json.loads((COMPONENT_DIR / path).read_text())
    init = strings["options"]["step"]["init"]
    assert init["data"][CONF_SCAN_HACS] == "Analyze Marketplace (HACS) component updates"
    assert "Marketplace" in init["data_description"][CONF_SCAN_HACS]


def test_manifest_category() -> None:
    manifest = json.loads((COMPONENT_DIR / "manifest.json").read_text())
    assert manifest["categories"] == ["computers_network.cloud_developer_tools"]


async def test_options_flow_honors_stored_key(hass: HomeAssistant, mock_conversation_agent) -> None:
    """An entry saved before the rename shows (and keeps) its stored choice."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        options={CONF_SCAN_HACS: False},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    field = next(key for key in result["data_schema"].schema if key == CONF_SCAN_HACS)
    assert field.default() is False

    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_HACS] is False


async def test_stored_key_false_skips_marketplace_updates(hass: HomeAssistant) -> None:
    hass.states.async_set("update.card_mod", "on", {"latest_version": "4.0.0", "release_url": GITHUB_RELEASE})

    off = _coordinator(hass, **{CONF_SCAN_HACS: False})
    with patch.object(off, "async_analyze_hacs_update", AsyncMock()) as analyze:
        await off.async_analyze_available_update()
    analyze.assert_not_awaited()

    on = _coordinator(hass)  # default: on
    with patch.object(on, "async_analyze_hacs_update", AsyncMock()) as analyze:
        await on.async_analyze_available_update()
    analyze.assert_awaited_once_with("update.card_mod", automatic=False)


# --- Recognizing Marketplace update entities ---


async def test_is_component_update(hass: HomeAssistant) -> None:
    marketplace = _register(hass, "marketplace", "card_mod")
    hacs = _register(hass, "hacs", "browser_mod")
    other = _register(hass, "esphome", "kitchen_firmware")

    # Marketplace / HACS entities count even without a GitHub release link
    assert _is_component_update(hass, State(marketplace, "off", {"release_url": None}))
    assert _is_component_update(hass, State(hacs, "off", {}))
    # Anything else needs a GitHub release_url, as before
    assert _is_component_update(hass, State(other, "on", {"release_url": GITHUB_RELEASE}))
    assert not _is_component_update(hass, State(other, "on", {"release_url": "https://esphome.io/changelog/"}))
    assert not _is_component_update(hass, State("update.unregistered", "on", {}))


async def test_component_list_includes_marketplace_entities(hass: HomeAssistant) -> None:
    card_mod = _register(hass, "marketplace", "card_mod")
    hass.states.async_set(
        card_mod,
        "on",
        {"friendly_name": "card-mod update", "installed_version": "3.4.4", "release_url": None},
    )
    hass.states.async_set(
        "update.powercalc_update",
        "off",
        {"friendly_name": "Powercalc update", "installed_version": "1.26.1", "release_url": GITHUB_RELEASE},
    )
    hass.states.async_set("update.kitchen_firmware", "off", {"release_url": "https://esphome.io/changelog/"})
    hass.states.async_set(HA_CORE_UPDATE_ENTITY, "on", {"friendly_name": "Home Assistant Core update"})

    listing = _coordinator(hass)._get_hacs_component_list()
    assert listing.splitlines() == [
        "- card-mod update: 3.4.4 (update available)",
        "- Powercalc update: 1.26.1",
    ]


async def test_component_list_empty(hass: HomeAssistant) -> None:
    assert _coordinator(hass)._get_hacs_component_list() == "No Marketplace (HACS) components detected."


async def test_marketplace_update_without_github_link_is_logged(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entity_id = _register(hass, "marketplace", "card_mod")
    hass.states.async_set(entity_id, "on", {"installed_version": "3.4.4", "latest_version": "4.0.0"})
    coordinator = _coordinator(hass)
    with patch.object(coordinator, "_run_analysis", AsyncMock()) as run:
        await coordinator.async_analyze_available_update()
    run.assert_not_awaited()
    assert f"Cannot analyze update {entity_id}" in caplog.text


# --- Names come from State.name, not the friendly_name attribute ---


async def test_component_name_is_unchanged(hass: HomeAssistant) -> None:
    """The analyzed name is still the entity's display name."""
    hass.states.async_set(
        "update.card_mod",
        "on",
        {
            "friendly_name": "card-mod update",
            "installed_version": "3.4.4",
            "latest_version": "4.0.0",
            "release_url": GITHUB_RELEASE,
        },
    )
    coordinator = _coordinator(hass)
    with patch.object(coordinator, "_run_analysis", AsyncMock()) as run:
        await coordinator.async_analyze_hacs_update("update.card_mod")
    run.assert_awaited_once_with(
        upgrade_type=MARKETPLACE_UPGRADE_TYPE,
        component_name="card-mod update",
        current_version="3.4.4",
        target_version="4.0.0",
        repo="thomasloven/lovelace-card-mod",
        entity_id="update.card_mod",
        automatic=False,
    )


async def test_names_do_not_read_friendly_name_attribute(hass: HomeAssistant) -> None:
    """Once friendly_name is gone, the name Home Assistant computes is still used."""
    hass.states.async_set(
        "update.card_mod",
        "on",
        {"installed_version": "3.4.4", "latest_version": "4.0.0", "release_url": GITHUB_RELEASE},
    )
    hass.states.async_set("automation.porch_lights", "on")
    coordinator = _coordinator(hass)
    with (
        patch.object(State, "name", new_callable=PropertyMock, return_value="card-mod"),
        patch.object(coordinator, "_run_analysis", AsyncMock()) as run,
    ):
        await coordinator.async_analyze_hacs_update("update.card_mod")
        listing = coordinator._get_hacs_component_list()
        automations = async_get_automation_summaries(hass)
    assert run.await_args.kwargs["component_name"] == "card-mod"
    assert listing == "- card-mod: 3.4.4 (update available)"
    assert automations == "- card-mod (on)"
