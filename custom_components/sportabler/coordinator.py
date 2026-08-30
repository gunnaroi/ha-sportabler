"""Data update coordinator for Sportabler."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AblerApiClient, AblerAuthError, AblerApiError
from .const import (
    CONF_REFRESH_TOKEN,
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
        super().__init__(
            hass,
            _LOGGER,
            name="Sportabler",
            update_interval=timedelta(minutes=DEFAULT_SCAN_INTERVAL_MINUTES),
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            me = await self.client.async_get_me()
            now = dt_util.utcnow()
            time_after = (now - timedelta(days=SCHEDULE_LOOKBACK_DAYS)).isoformat()
            time_before = (now + timedelta(days=SCHEDULE_LOOKAHEAD_DAYS)).isoformat()
            raw_events = await self.client.async_get_schedule(time_after, time_before)
        except AblerAuthError as err:
            raise UpdateFailed(
                "Sportabler session expired - re-authenticate in the integration options"
            ) from err
        except AblerApiError as err:
            raise UpdateFailed(str(err)) from err
        finally:
            self._maybe_persist_refresh_token()

        children = {child["id"]: child for child in me.get("children", [])}
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
