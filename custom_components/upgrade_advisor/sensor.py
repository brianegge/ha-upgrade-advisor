"""Sensor platform for Upgrade Advisor."""

from __future__ import annotations

from typing import Any, ClassVar

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

type UpgradeAdvisorConfigEntry = ConfigEntry


async def async_setup_entry(
    hass: HomeAssistant, entry: UpgradeAdvisorConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Upgrade Advisor sensors from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    async_add_entities(
        [
            UpgradeAdvisorStatusSensor(coordinator, entry),
            UpgradeAdvisorRiskSensor(coordinator, entry),
        ]
    )


class UpgradeAdvisorSensorBase(SensorEntity):
    """Base class for Upgrade Advisor sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: Any, entry: UpgradeAdvisorConfigEntry) -> None:
        """Initialize."""
        self.coordinator = coordinator
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Upgrade Advisor",
            manufacturer="Home Assistant Community",
        )

    async def async_added_to_hass(self) -> None:
        """Register update listener."""
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()


class UpgradeAdvisorStatusSensor(UpgradeAdvisorSensorBase):
    """Sensor showing analysis status."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = ["idle", "analyzing", "report_ready", "error"]
    _attr_translation_key = "status"

    def __init__(self, coordinator: Any, entry: UpgradeAdvisorConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str | None:
        """Return the current status."""
        return self.coordinator.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return report details."""
        attrs: dict[str, Any] = {
            "current_version": self.coordinator.current_version,
            "available_version": self.coordinator.available_version,
            "last_analysis": self.coordinator.last_analysis,
            "breaking_change_count": self.coordinator.breaking_change_count,
        }
        if self.coordinator.report:
            attrs["report"] = self.coordinator.report
        return attrs


class UpgradeAdvisorRiskSensor(UpgradeAdvisorSensorBase):
    """Sensor showing risk level of available upgrade."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = ["unknown", "low", "medium", "high"]
    _attr_translation_key = "risk"

    def __init__(self, coordinator: Any, entry: UpgradeAdvisorConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_risk"

    @property
    def native_value(self) -> str | None:
        """Return the risk level."""
        return self.coordinator.risk_level
