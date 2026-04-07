"""Shared fixtures for Upgrade Advisor tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import loader
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

from custom_components.upgrade_advisor.const import CONF_AGENT_ID, DOMAIN

MOCK_AGENT_ID = "conversation.mock_ai_agent"

MOCK_CONFIG = {
    CONF_AGENT_ID: MOCK_AGENT_ID,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(hass: HomeAssistant) -> None:
    """Enable custom integrations and mark conversation as set up."""
    hass.data.pop(loader.DATA_CUSTOM_COMPONENTS)
    mock_component(hass, "conversation")


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Create and add a mock config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Mock AI Agent",
        data=MOCK_CONFIG.copy(),
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_conversation_agent():
    """Mock conversation agent info."""
    agent_info = MagicMock()
    agent_info.name = "Mock AI Agent"
    agent_info.id = MOCK_AGENT_ID

    with patch(
        "homeassistant.components.conversation.async_get_agent_info",
        return_value=agent_info,
    ) as mock:
        yield mock


@pytest.fixture
def mock_converse():
    """Mock the conversation.async_converse function."""
    response = MagicMock()
    response.response.response_type.value = "action_done"
    response.response.speech = {
        "plain": {
            "speech": (
                "## Analysis Report\n\n"
                "### Breaking Changes\nNone found.\n\n"
                "### Risk Assessment\nLow risk.\n\n"
                "RISK_LEVEL: Low\n"
                "BREAKING_CHANGES: 0"
            )
        }
    }

    with patch(
        "homeassistant.components.conversation.async_converse",
        new_callable=AsyncMock,
        return_value=response,
    ) as mock:
        yield mock


@pytest.fixture
def mock_github_release():
    """Mock GitHub release notes fetch."""
    with patch(
        "custom_components.upgrade_advisor.github.async_get_release_notes",
        new_callable=AsyncMock,
        return_value="## What's Changed\n- Fixed a bug\n- Added a feature",
    ) as mock:
        yield mock


MOCK_RELEASE_NOTES = """## What's Changed

### Breaking Changes
- The `xyz` integration now requires version 2.0 of its library

### New Features
- Added support for `abc` devices
"""
