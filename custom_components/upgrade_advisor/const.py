"""Constants for the Upgrade Advisor integration."""

from __future__ import annotations

from datetime import timedelta
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
CONF_INCLUDE_ISSUES: Final = "include_github_issues"
CONF_MIN_RELEASE_AGE_DAYS: Final = "min_release_age_days"
CONF_MIN_ADOPTION_PCT: Final = "min_adoption_pct"

DEFAULT_SCAN_ON_UPDATE: Final = True
DEFAULT_SCAN_HACS: Final = True
DEFAULT_CREATE_REPAIRS: Final = True
DEFAULT_INCLUDE_AUTOMATIONS: Final = True
DEFAULT_INCLUDE_ADDONS: Final = True
DEFAULT_DASHBOARD_PATH: Final = ""
DEFAULT_POST_UPGRADE_CHECK: Final = True
DEFAULT_INCLUDE_ISSUES: Final = True
DEFAULT_MIN_RELEASE_AGE_DAYS: Final = 0
DEFAULT_MIN_ADOPTION_PCT: Final = 0

# How often updates held back by the release-age / adoption gate are re-checked.
DEFERRED_RECHECK_INTERVAL: Final = timedelta(hours=3)
# Past this age the adoption threshold is waived, so a niche integration with
# few reporting installs is not held back forever.
ADOPTION_GATE_MAX_AGE_DAYS: Final = 30

STARTUP_DELAY_SECONDS: Final = 300  # Wait 5 minutes for entities to become available
PENDING_STORAGE_KEY: Final = f"{DOMAIN}_pending"
PENDING_STORAGE_VERSION: Final = 1
PENDING_RETENTION_DAYS: Final = 14

HA_CORE_UPDATE_ENTITY: Final = "update.home_assistant_core_update"
HA_CORE_REPO: Final = "home-assistant/core"
GITHUB_API_BASE: Final = "https://api.github.com"

PLATFORMS: Final[list[str]] = ["sensor", "event"]
