"""The Sportabler (Abler) integration."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.service import async_register_admin_service

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


MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("first", default=5): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
        vol.Optional("after"): cv.string,
    }
)
COMMENTS_SCHEMA = MESSAGE_SCHEMA.extend(
    {vol.Required("post_id"): vol.All(vol.Coerce(int), vol.Range(min=1))}
)
CONVERSATION_LIST_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("first", default=20): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
        vol.Optional("after"): cv.string,
    }
)
CONVERSATION_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Required("conversation_id"): cv.string,
        vol.Optional("first", default=30): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=30)
        ),
        vol.Optional("after"): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register explicit, admin-only response actions."""

    def selected_client(call: ServiceCall):
        entries = hass.data.get(DOMAIN, {})
        entry_id = call.data.get("entry_id")
        if entry_id:
            selected = entries.get(entry_id)
            if selected is None:
                raise HomeAssistantError("Unknown Sportabler entry ID")
        elif len(entries) == 1:
            selected = next(iter(entries.values()))
        else:
            raise HomeAssistantError(
                "Specify entry_id when multiple accounts are configured"
            )
        return selected

    async def get_feed(call: ServiceCall) -> dict:
        selected = selected_client(call)
        try:
            return await selected["client"].async_get_news_feed(
                call.data["first"], call.data.get("after")
            )
        except AblerAuthError as err:
            selected["coordinator"].entry.async_start_reauth(hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err

    async def get_comments(call: ServiceCall) -> dict:
        selected = selected_client(call)
        try:
            return await selected["client"].async_get_post_comments(
                call.data["post_id"], call.data["first"], call.data.get("after")
            )
        except AblerAuthError as err:
            selected["coordinator"].entry.async_start_reauth(hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err

    async def get_conversations(call: ServiceCall) -> dict:
        selected = selected_client(call)
        try:
            return await selected["client"].async_get_conversations(
                call.data["first"], call.data.get("after")
            )
        except AblerAuthError as err:
            selected["coordinator"].entry.async_start_reauth(hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err

    async def get_conversation(call: ServiceCall) -> dict:
        selected = selected_client(call)
        try:
            return await selected["client"].async_get_conversation_messages(
                call.data["conversation_id"], call.data["first"], call.data.get("after")
            )
        except AblerAuthError as err:
            selected["coordinator"].entry.async_start_reauth(hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err

    for name, handler, schema in (
        ("get_feed", get_feed, MESSAGE_SCHEMA),
        ("get_post_comments", get_comments, COMMENTS_SCHEMA),
        ("get_conversations", get_conversations, CONVERSATION_LIST_SCHEMA),
        ("get_conversation_messages", get_conversation, CONVERSATION_SCHEMA),
    ):
        async_register_admin_service(
            hass,
            DOMAIN,
            name,
            handler,
            schema,
            supports_response=SupportsResponse.ONLY,
        )
    return True


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
