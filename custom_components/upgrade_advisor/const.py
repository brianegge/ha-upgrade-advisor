"""Constants for the Upgrade Advisor integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "upgrade_advisor"

CONF_AGENT_ID: Final = "agent_id"
CONF_SCAN_ON_UPDATE: Final = "scan_on_update_available"
CONF_SCAN_HACS: Final = "scan_hacs_updates"
CONF_CREATE_REPAIRS: Final = "create_repair_issues"
CONF_INCLUDE_AUTOMATIONS: Final = "include_automations"
CONF_INCLUDE_ADDONS: Final = "include_addons"
CONF_DASHBOARD_PATH: Final = "dashboard_path"

DEFAULT_SCAN_ON_UPDATE: Final = True
DEFAULT_SCAN_HACS: Final = True
DEFAULT_CREATE_REPAIRS: Final = True
DEFAULT_INCLUDE_AUTOMATIONS: Final = True
DEFAULT_INCLUDE_ADDONS: Final = True
DEFAULT_DASHBOARD_PATH: Final = "upgrade-advisor"

HA_CORE_UPDATE_ENTITY: Final = "update.home_assistant_core_update"
HA_CORE_REPO: Final = "home-assistant/core"
GITHUB_API_BASE: Final = "https://api.github.com"

PLATFORMS: Final[list[str]] = ["sensor", "event"]
