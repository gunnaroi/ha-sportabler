"""Calendar platform for Sportabler."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SportablerCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SportablerCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        SportablerCalendar(coordinator, child_id)
        for child_id in coordinator.data["children"]
    )


class SportablerCalendar(CoordinatorEntity[SportablerCoordinator], CalendarEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: SportablerCoordinator, child_id: str) -> None:
        super().__init__(coordinator)
        self._child_id = child_id
        self._attr_unique_id = f"{child_id}_calendar"
        child = coordinator.data["children"][child_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, child_id)},
            name=child.get("displayName"),
            manufacturer="Sportabler",
        )

    def _events(self) -> list[dict]:
        return self.coordinator.data["events_by_child"].get(self._child_id, [])

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.utcnow()
        for event in self._events():
            if event["end"] is not None and event["end"] >= now:
                return _to_calendar_event(event)
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            _to_calendar_event(event)
            for event in self._events()
            if event["start"] < end_date and (event["end"] or event["start"]) > start_date
        ]


def _to_calendar_event(event: dict) -> CalendarEvent:
    return CalendarEvent(
        start=event["start"],
        end=event["end"] or event["start"],
        summary=event["name"],
        description=event.get("description"),
        location=event.get("location"),
        uid=event["id"],
    )
