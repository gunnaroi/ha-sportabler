"""Sensor platform for Sportabler."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import ATTENDANCE_STATUS_MAP, DOMAIN
from .coordinator import SportablerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SportablerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        SportablerNextActivitySensor(coordinator, child_id)
        for child_id in coordinator.data["children"]
    )


class SportablerNextActivitySensor(
    CoordinatorEntity[SportablerCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "Next activity"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SportablerCoordinator, child_id: str) -> None:
        super().__init__(coordinator)
        self._child_id = child_id
        self._attr_unique_id = f"{child_id}_next_activity"
        child = coordinator.data["children"][child_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, child_id)},
            name=child.get("displayName"),
            manufacturer="Sportabler",
        )

    def _next_event(self) -> dict | None:
        now = dt_util.utcnow()
        for event in self.coordinator.data["events_by_child"].get(self._child_id, []):
            if event["start"] >= now:
                return event
        return None

    @property
    def native_value(self):
        event = self._next_event()
        return event["start"] if event else None

    @property
    def extra_state_attributes(self):
        event = self._next_event()
        if not event:
            return {}
        return {
            "name": event["name"],
            "end": event["end"],
            "location": event.get("location"),
            "team": event.get("team"),
            "organization": event.get("organization"),
            "status": event.get("status"),
            "attendance_status": ATTENDANCE_STATUS_MAP.get(
                event.get("attendance_status"), event.get("attendance_status")
            ),
            "event_id": event["id"],
        }
