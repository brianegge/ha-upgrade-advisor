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
CONF_POST_UPGRADE_CHECK: Final = "post_upgrade_check"

DEFAULT_SCAN_ON_UPDATE: Final = True
DEFAULT_SCAN_HACS: Final = True
DEFAULT_CREATE_REPAIRS: Final = True
DEFAULT_INCLUDE_AUTOMATIONS: Final = True
DEFAULT_INCLUDE_ADDONS: Final = True
DEFAULT_DASHBOARD_PATH: Final = ""
DEFAULT_POST_UPGRADE_CHECK: Final = True

STARTUP_DELAY_SECONDS: Final = 300  # Wait 5 minutes for entities to become available
PENDING_STORAGE_KEY: Final = f"{DOMAIN}_pending"
PENDING_STORAGE_VERSION: Final = 1
PENDING_RETENTION_DAYS: Final = 14

HA_CORE_UPDATE_ENTITY: Final = "update.home_assistant_core_update"
HA_CORE_REPO: Final = "home-assistant/core"
GITHUB_API_BASE: Final = "https://api.github.com"

PLATFORMS: Final[list[str]] = ["sensor", "event"]
