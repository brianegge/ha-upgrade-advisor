"""Tests for Upgrade Advisor config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.upgrade_advisor.const import CONF_AGENT_ID, DOMAIN

from .conftest import MOCK_AGENT_ID, MOCK_CONFIG


async def test_user_flow_shows_form(hass: HomeAssistant) -> None:
    """Test that the user flow shows a form initially."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_user_flow_success(hass: HomeAssistant, mock_conversation_agent) -> None:
    """Test successful user config flow creates entry."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_AGENT_ID: MOCK_AGENT_ID},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mock AI Agent"
    assert result["data"] == MOCK_CONFIG


async def test_user_flow_agent_not_found(hass: HomeAssistant) -> None:
    """Test user flow with invalid agent shows error."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    with patch(
        "homeassistant.components.conversation.async_get_agent_info",
        return_value=None,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_AGENT_ID: "conversation.nonexistent"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "agent_not_found"}


async def test_user_flow_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_conversation_agent
) -> None:
    """Test user flow aborts when already configured."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_AGENT_ID: MOCK_AGENT_ID},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Test the options flow saves preferences."""
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "scan_on_update_available": False,
            "scan_hacs_updates": True,
            "create_repair_issues": False,
            "include_automations": True,
            "include_addons": False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["scan_on_update_available"] is False
    assert result["data"]["create_repair_issues"] is False
    assert result["data"]["include_addons"] is False
