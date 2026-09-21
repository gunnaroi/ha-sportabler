"""The Sportabler (Abler) integration."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .api import AblerApiClient, AblerApiError, AblerAuthError
from .const import (
    ATTR_CHILD_ID,
    ATTR_EVENT_ID,
    ATTR_STATUS,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    STATUS_GOING,
    STATUS_NOT_GOING,
)
from .coordinator import SportablerCoordinator

PLATFORMS = ["calendar", "sensor"]

SET_ATTENDANCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CHILD_ID): cv.string,
        vol.Required(ATTR_EVENT_ID): cv.string,
        vol.Required(ATTR_STATUS): vol.In([STATUS_GOING, STATUS_NOT_GOING]),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar())

    @callback
    def persist_token() -> None:
        if client.refresh_token != entry.data.get(CONF_REFRESH_TOKEN):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: client.refresh_token}
            )

    client = AblerApiClient(session, entry.data[CONF_REFRESH_TOKEN], persist_token)
    coordinator = SportablerCoordinator(hass, entry, client)

    try:
        await coordinator.async_config_entry_first_refresh()
    except BaseException:
        await session.close()
        raise

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "session": session,
    }

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await session.close()
        raise

    @callback
    def update_local_time(_now) -> None:
        # Advance time-dependent entities even in manual-only mode. No API calls.
        coordinator.async_update_listeners()

    entry.async_on_unload(
        async_track_time_interval(hass, update_local_time, timedelta(minutes=1))
    )

    async def _handle_set_attendance(call: ServiceCall) -> None:
        child_id = call.data[ATTR_CHILD_ID]
        candidates = [
            data["coordinator"]
            for data in hass.data[DOMAIN].values()
            if child_id in data["coordinator"].data["children"]
        ]
        if len(candidates) != 1:
            raise HomeAssistantError(
                "Child must belong to exactly one configured Sportabler account"
            )
        selected = candidates[0]
        try:
            await selected.async_set_attendance(
                child_id, call.data[ATTR_EVENT_ID], call.data[ATTR_STATUS]
            )
        except AblerAuthError as err:
            selected.entry.async_start_reauth(hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err

    hass.services.async_register(
        DOMAIN, "set_attendance", _handle_set_attendance, schema=SET_ATTENDANCE_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        await entry_data["session"].close()
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "set_attendance")
    return unload_ok
