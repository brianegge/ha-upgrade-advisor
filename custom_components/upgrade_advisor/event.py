"""Event platform for Upgrade Advisor."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

type UpgradeAdvisorConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: UpgradeAdvisorConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Upgrade Advisor event entity from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entity = UpgradeAdvisorReportEvent(coordinator, entry)
    hass.data[DOMAIN][entry.entry_id]["event_entity"] = entity
    async_add_entities([entity])


class UpgradeAdvisorReportEvent(EventEntity):
    """Event entity that fires when a new report is generated."""

    _attr_has_entity_name = True
    _attr_translation_key = "report"
    _attr_event_types: ClassVar[list[str]] = ["report_generated"]

    def __init__(self, coordinator: Any, entry: UpgradeAdvisorConfigEntry) -> None:
        """Initialize."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_report"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Upgrade Advisor",
            manufacturer="Home Assistant Community",
        )

    def fire_report_event(self, report_data: dict[str, Any]) -> None:
        """Fire the report event with analysis data."""
        self._trigger_event("report_generated", report_data)
        self.async_write_ha_state()
