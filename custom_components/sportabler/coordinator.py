"""Data update coordinator for Sportabler."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AblerApiClient, AblerApiError, AblerAuthError
from .const import (
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    SCHEDULE_LOOKAHEAD_DAYS,
    SCHEDULE_LOOKBACK_DAYS,
)

_LOGGER = logging.getLogger(__name__)


class SportablerCoordinator(DataUpdateCoordinator):
    """Fetches children + upcoming schedule from Sportabler."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: AblerApiClient
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name="Sportabler",
            config_entry=entry,
            update_interval=timedelta(minutes=minutes) if minutes else None,
        )
        self.entry = entry
        self.client = client
        self._me = None
        self._profile_updated = 0.0
        self._operation_lock = asyncio.Lock()

    async def _async_update_data(self) -> dict:
        async with self._operation_lock:
            return await self._async_fetch_data()

    async def _async_fetch_data(self) -> dict:
        try:
            if self._me is None or time.monotonic() - self._profile_updated >= 86400:
                self._me = await self.client.async_get_me()
                self._profile_updated = time.monotonic()
            me = self._me
            children = {child["id"]: child for child in me.get("children", [])}
            now = dt_util.utcnow()
            time_after = (now - timedelta(days=SCHEDULE_LOOKBACK_DAYS)).isoformat()
            time_before = (now + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)).isoformat()
            raw_events = await self.client.async_get_schedule(
                time_after, time_before, child_ids=list(children)
            )
        except AblerAuthError as err:
            raise ConfigEntryAuthFailed(
                "Sportabler session expired - re-authenticate the integration"
            ) from err
        except AblerApiError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._maybe_persist_refresh_token()

        events_by_child: dict[str, list[dict]] = {child_id: [] for child_id in children}

        for event in raw_events:
            start = _parse_dt(event.get("from"))
            end = _parse_dt(event.get("to"))
            if start is None:
                continue
            age_group = event.get("ageGroup") or {}
            organization = age_group.get("organization") or {}
            for participant in event.get("familyPlayers") or []:
                player = participant.get("player") or {}
                child_id = player.get("id")
                if child_id not in events_by_child:
                    continue
                events_by_child[child_id].append(
                    {
                        "id": event.get("id"),
                        "name": event.get("name"),
                        "description": event.get("description"),
                        "start": start,
                        "end": end,
                        "location": event.get("locationAddress"),
                        "location_details": event.get("locationDetails"),
                        "status": event.get("status"),
                        "team": age_group.get("name"),
                        "organization": organization.get("name"),
                        "attendance_status": participant.get("status"),
                        "event_player_id": participant.get("id"),
                    }
                )

        for child_id in events_by_child:
            events_by_child[child_id].sort(key=lambda e: e["start"])

        return {"children": children, "events_by_child": events_by_child}

    async def async_set_attendance(
        self, child_id: str, event_id: str, status: str
    ) -> None:
        """Serialize writes with refreshes and cache the confirmed mutation result."""
        async with self._operation_lock:
            events = self.data["events_by_child"].get(child_id, [])
            event = next((item for item in events if item["id"] == event_id), None)
            if event is None:
                raise AblerApiError(
                    "Event is not in this child's cached schedule; refresh first"
                )
            try:
                result = await self.client.async_set_attendance(
                    event_id, child_id, status
                )
            finally:
                self._maybe_persist_refresh_token()
            confirmed = result.get("eventPlayer") or {}
            if (
                result.get("eventId") != event_id
                or not isinstance(confirmed, dict)
                or "status" not in confirmed
            ):
                raise AblerApiError(
                    "Attendance response was incomplete; refresh to confirm"
                )
            updated_events = [
                {**item, "attendance_status": confirmed["status"]}
                if item["id"] == event_id
                else item
                for item in events
            ]
            self.async_set_updated_data(
                {
                    **self.data,
                    "events_by_child": {
                        **self.data["events_by_child"],
                        child_id: updated_events,
                    },
                }
            )

    def _maybe_persist_refresh_token(self) -> None:
        new_token = self.client.refresh_token
        if new_token and new_token != self.entry.data.get(CONF_REFRESH_TOKEN):
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, CONF_REFRESH_TOKEN: new_token},
            )


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(value)
