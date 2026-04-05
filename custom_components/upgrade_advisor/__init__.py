"""Upgrade Advisor integration — AI-powered upgrade analysis for Home Assistant."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from aiohttp import ClientSession
from homeassistant.components.persistent_notification import async_create as async_create_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_state_change_event

from .analyzer import AnalysisResult, async_analyze, build_prompt
from .const import (
    CONF_AGENT_ID,
    CONF_CREATE_REPAIRS,
    CONF_INCLUDE_ADDONS,
    CONF_INCLUDE_AUTOMATIONS,
    CONF_SCAN_HACS,
    CONF_SCAN_ON_UPDATE,
    DEFAULT_CREATE_REPAIRS,
    DEFAULT_INCLUDE_ADDONS,
    DEFAULT_INCLUDE_AUTOMATIONS,
    DEFAULT_SCAN_HACS,
    DEFAULT_SCAN_ON_UPDATE,
    DOMAIN,
    HA_CORE_UPDATE_ENTITY,
    PLATFORMS,
)
from .github import async_get_ha_release_notes_range, async_get_hacs_release_notes
from .services import async_register_services, async_unregister_services
from .summarize import build_installation_context

_LOGGER = logging.getLogger(__name__)


class UpgradeAdvisorCoordinator:
    """Coordinates upgrade analysis and stores results."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self._listeners: list[Any] = []

        # State
        self.status: str = "idle"
        self.risk_level: str = "unknown"
        self.report: str | None = None
        self.current_version: str | None = None
        self.available_version: str | None = None
        self.last_analysis: str | None = None
        self.breaking_change_count: int = 0

    @callback
    def async_add_listener(self, listener: Any) -> Any:
        """Add a listener for state updates."""
        self._listeners.append(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.remove(listener)

        return remove_listener

    @callback
    def _async_notify_listeners(self) -> None:
        """Notify all listeners of a state change."""
        for listener in self._listeners:
            listener()

    def _get_option(self, key: str, default: Any) -> Any:
        """Get an option value with fallback to default."""
        return self.entry.options.get(key, default)

    async def async_analyze_core_update(self) -> None:
        """Analyze only the HA core update."""
        state = self.hass.states.get(HA_CORE_UPDATE_ENTITY)
        if state is None or state.state != "on":
            _LOGGER.warning("No HA core update available to analyze")
            return

        current = state.attributes.get("installed_version", "")
        target = state.attributes.get("latest_version", "")
        if not target:
            return

        await self._run_analysis(
            upgrade_type="Home Assistant Core",
            component_name="Home Assistant",
            current_version=current,
            target_version=target,
            repo=None,
        )

    async def async_analyze_available_update(self) -> None:
        """Analyze all pending updates — HA core and HACS components."""
        analyzed = False

        # HA core update
        state = self.hass.states.get(HA_CORE_UPDATE_ENTITY)
        if state is not None and state.state == "on":
            current = state.attributes.get("installed_version", "")
            target = state.attributes.get("latest_version", "")
            if target:
                await self._run_analysis(
                    upgrade_type="Home Assistant Core",
                    component_name="Home Assistant",
                    current_version=current,
                    target_version=target,
                    repo=None,
                )
                analyzed = True

        # HACS / other component updates
        scan_hacs = self._get_option(CONF_SCAN_HACS, DEFAULT_SCAN_HACS)
        if scan_hacs:
            for update_state in self.hass.states.async_all("update"):
                if update_state.entity_id == HA_CORE_UPDATE_ENTITY:
                    continue
                if update_state.state != "on":
                    continue
                release_url = update_state.attributes.get("release_url") or ""
                if "github.com" not in release_url:
                    continue
                await self.async_analyze_hacs_update(update_state.entity_id)
                analyzed = True

        if not analyzed:
            _LOGGER.warning("No updates available to analyze")

    async def async_analyze_version(self, version: str) -> None:
        """Analyze a specific HA version."""
        current = self.hass.config.version or "unknown"
        await self._run_analysis(
            upgrade_type="Home Assistant Core",
            component_name="Home Assistant",
            current_version=current,
            target_version=version,
            repo=None,
        )

    async def async_analyze_hacs_update(self, entity_id: str) -> None:
        """Analyze a HACS component update."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state != "on":
            return

        current = state.attributes.get("installed_version", "")
        target = state.attributes.get("latest_version", "")
        title = state.attributes.get("friendly_name", entity_id)
        # HACS update entities store the release URL which contains the repo path
        release_url = state.attributes.get("release_url", "")
        repo = _extract_repo_from_url(release_url)

        if not target or not repo:
            _LOGGER.debug("Cannot analyze HACS update for %s: missing version or repo", entity_id)
            return

        await self._run_analysis(
            upgrade_type="HACS Component",
            component_name=title,
            current_version=current,
            target_version=target,
            repo=repo,
        )

    async def _run_analysis(
        self,
        upgrade_type: str,
        component_name: str,
        current_version: str,
        target_version: str,
        repo: str | None,
    ) -> None:
        """Run the full analysis pipeline."""
        self.status = "analyzing"
        self.current_version = current_version
        self.available_version = target_version
        self._async_notify_listeners()

        session: ClientSession = async_get_clientsession(self.hass)

        # Fetch release notes
        if repo is None:
            # HA core: fetch all releases between current and target
            release_notes = await async_get_ha_release_notes_range(session, current_version, target_version)
        else:
            release_notes = await async_get_hacs_release_notes(session, repo, target_version)

        if release_notes is None:
            release_notes = "Release notes not available."

        # Gather installation context
        include_automations = self._get_option(CONF_INCLUDE_AUTOMATIONS, DEFAULT_INCLUDE_AUTOMATIONS)
        include_addons = self._get_option(CONF_INCLUDE_ADDONS, DEFAULT_INCLUDE_ADDONS)
        context = build_installation_context(self.hass, include_automations, include_addons)

        # Build HACS component list
        hacs_components = self._get_hacs_component_list()

        # Build prompt
        prompt = build_prompt(
            upgrade_type=upgrade_type,
            component_name=component_name,
            current_version=current_version,
            target_version=target_version,
            release_notes=release_notes,
            context=context,
            hacs_components=hacs_components,
        )

        # Run analysis
        agent_id = self.entry.data[CONF_AGENT_ID]
        result = await async_analyze(
            self.hass,
            agent_id,
            prompt,
            upgrade_type,
            component_name,
            current_version,
            target_version,
        )

        # Store results
        self._store_result(result)

        # Output results
        if result.error:
            self.status = "error"
        else:
            self.status = "report_ready"
            await self._async_output_results(result)

        self._async_notify_listeners()

    def _store_result(self, result: AnalysisResult) -> None:
        """Store analysis results on the coordinator."""
        self.report = result.report if not result.error else f"Error: {result.error}"
        self.risk_level = result.risk_level
        self.breaking_change_count = result.breaking_change_count
        self.last_analysis = datetime.now(tz=UTC).isoformat()

    async def _async_output_results(self, result: AnalysisResult) -> None:
        """Create notifications, repair issues, and fire events."""
        # Persistent notification
        title = f"Upgrade Advisor: {result.component_name} {result.current_version} → {result.target_version}"
        if result.breaking_change_count > 0:
            message = (
                f"**Risk: {result.risk_level.upper()}** | "
                f"**Breaking changes: {result.breaking_change_count}**\n\n"
                f"See Settings → Repairs for details, or add a Markdown card with:\n"
                f'`{{{{ state_attr("sensor.upgrade_advisor_status", "report") }}}}`'
            )
        else:
            message = (
                f"**Risk: {result.risk_level.upper()}** | "
                f"No breaking changes found for your installation.\n\n"
                f"View the full report with a Markdown card:\n"
                f'`{{{{ state_attr("sensor.upgrade_advisor_status", "report") }}}}`'
            )
        # Use component name in notification_id so each update gets its own notification
        safe_name = result.component_name.lower().replace(" ", "_")[:30]
        async_create_notification(self.hass, message, title=title, notification_id=f"{DOMAIN}_{safe_name}")

        # Repair issues — one per component+version
        create_repairs = self._get_option(CONF_CREATE_REPAIRS, DEFAULT_CREATE_REPAIRS)
        if create_repairs and result.breaking_change_count > 0:
            ir.async_create_issue(
                self.hass,
                domain=DOMAIN,
                issue_id=f"breaking_changes_{safe_name}_{result.target_version}",
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="breaking_changes_found",
                translation_placeholders={
                    "component": result.component_name,
                    "version": result.target_version,
                    "count": str(result.breaking_change_count),
                    "risk": result.risk_level,
                },
            )

        # Fire event
        event_entity = self.hass.data[DOMAIN].get(self.entry.entry_id, {}).get("event_entity")
        if event_entity is not None:
            event_entity.fire_report_event(
                {
                    "upgrade_type": result.upgrade_type,
                    "component_name": result.component_name,
                    "current_version": result.current_version,
                    "target_version": result.target_version,
                    "risk_level": result.risk_level,
                    "breaking_change_count": result.breaking_change_count,
                    "report": result.report,
                }
            )

    def _get_hacs_component_list(self) -> str:
        """Get a list of installed HACS components from update entities."""
        lines: list[str] = []
        for state in self.hass.states.async_all("update"):
            entity_id = state.entity_id
            # HACS update entities follow a naming pattern
            release_url = state.attributes.get("release_url") or ""
            if "hacs" not in entity_id and not release_url.startswith("https://github.com"):
                continue
            name = state.attributes.get("friendly_name", entity_id)
            installed = state.attributes.get("installed_version", "?")
            update_available = " (update available)" if state.state == "on" else ""
            lines.append(f"- {name}: {installed}{update_available}")

        return "\n".join(lines) if lines else "No HACS components detected."


def _extract_repo_from_url(url: str) -> str | None:
    """Extract owner/repo from a GitHub URL."""
    # e.g., https://github.com/owner/repo/releases/tag/v1.0.0
    if not url or "github.com" not in url:
        return None
    parts = url.split("github.com/")
    if len(parts) < 2:
        return None
    path_parts = parts[1].split("/")
    if len(path_parts) >= 2:
        return f"{path_parts[0]}/{path_parts[1]}"
    return None


def _cleanup_stale_repairs(hass: HomeAssistant) -> None:
    """Remove repair issues from older versions that used different issue_id formats."""
    issue_reg = ir.async_get(hass)
    stale = [
        issue_id
        for (domain, issue_id) in issue_reg.issues
        if domain == DOMAIN and not issue_id.startswith("breaking_changes_")
    ]
    # Also clean up old format: "breaking_changes_{version}" (no component name)
    for domain, issue_id in issue_reg.issues:
        if domain == DOMAIN and issue_id.startswith("breaking_changes_"):
            # Old format had just version like "breaking_changes_2026.4.0"
            # New format has component: "breaking_changes_home_assistant_2026.4.0"
            parts = issue_id.replace("breaking_changes_", "").split("_")
            # If it looks like just a version number (starts with digit), it's old format
            if parts and parts[0][:1].isdigit():
                stale.append(issue_id)
    for issue_id in stale:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        _LOGGER.info("Cleaned up stale repair issue: %s", issue_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Upgrade Advisor from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Clean up stale repair issues from older versions that used different issue_id formats
    _cleanup_stale_repairs(hass)

    coordinator = UpgradeAdvisorCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = {"coordinator": coordinator}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (only once)
    if len(hass.data[DOMAIN]) == 1:
        async_register_services(hass)

    # Set up update entity listeners
    _setup_update_listeners(hass, entry, coordinator)

    return True


@callback
def _setup_update_listeners(hass: HomeAssistant, entry: ConfigEntry, coordinator: UpgradeAdvisorCoordinator) -> None:
    """Set up listeners for update entity state changes.

    On startup, entities transition from None → current state, which looks like a
    new update. Instead of reacting to each one individually (causing races), we
    track whether startup has completed and handle the two cases differently:
    - Startup: ignore individual transitions, run one sequential scan of all updates
    - Runtime: react to individual transitions as they happen
    """
    startup_complete = False

    @callback
    def _on_update_available(event: Event) -> None:
        """Handle update entity state change (HA core only)."""
        if not startup_complete:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state != "on":
            return
        if old_state is not None and old_state.state == "on":
            return
        scan_on_update = entry.options.get(CONF_SCAN_ON_UPDATE, DEFAULT_SCAN_ON_UPDATE)
        if scan_on_update:
            hass.async_create_task(coordinator.async_analyze_core_update())

    @callback
    def _on_any_state_change(event: Event) -> None:
        """Watch for HACS update entities becoming available at runtime."""
        if not startup_complete:
            return
        entity_id = event.data.get("entity_id", "")
        if not entity_id.startswith("update.") or entity_id == HA_CORE_UPDATE_ENTITY:
            return
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if new_state is None or new_state.state != "on":
            return
        if old_state is not None and old_state.state == "on":
            return
        release_url = new_state.attributes.get("release_url") or ""
        if "github.com" not in release_url:
            return
        if entry.options.get(CONF_SCAN_HACS, DEFAULT_SCAN_HACS):
            hass.async_create_task(coordinator.async_analyze_hacs_update(entity_id))

    entry.async_on_unload(async_track_state_change_event(hass, [HA_CORE_UPDATE_ENTITY], _on_update_available))
    entry.async_on_unload(hass.bus.async_listen("state_changed", _on_any_state_change))

    async def _startup_scan(_event: Event | None = None) -> None:
        """Run a single sequential scan of all pending updates after startup."""
        nonlocal startup_complete
        scan_on_update = entry.options.get(CONF_SCAN_ON_UPDATE, DEFAULT_SCAN_ON_UPDATE)
        if scan_on_update:
            await coordinator.async_analyze_available_update()
        startup_complete = True

    # Run the startup scan after HA is fully started
    if hass.is_running:
        hass.async_create_task(_startup_scan())
    else:
        hass.bus.async_listen_once("homeassistant_started", _startup_scan)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            async_unregister_services(hass)
            hass.data.pop(DOMAIN)
    return unload_ok
