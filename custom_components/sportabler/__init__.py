"""The Sportabler (Abler) integration."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .api import AblerApiClient, AblerApiError
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
    client = AblerApiClient(session, entry.data[CONF_REFRESH_TOKEN])
    coordinator = SportablerCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "session": session,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_set_attendance(call: ServiceCall) -> None:
        for entry_data in hass.data[DOMAIN].values():
            try:
                await entry_data["client"].async_set_attendance(
                    call.data[ATTR_EVENT_ID],
                    call.data[ATTR_CHILD_ID],
                    call.data[ATTR_STATUS],
                )
            except AblerApiError as err:
                raise HomeAssistantError(f"Sportabler: {err}") from err
            await entry_data["coordinator"].async_request_refresh()
            return

    hass.services.async_register(
        DOMAIN, "set_attendance", _handle_set_attendance, schema=SET_ATTENDANCE_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        await entry_data["session"].close()
    return unload_ok
