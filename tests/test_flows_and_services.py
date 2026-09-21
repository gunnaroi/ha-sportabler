"""Exercise configuration and service routing with Home Assistant helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import AbortFlow
from homeassistant.exceptions import HomeAssistantError

from custom_components import sportabler
from custom_components.sportabler.api import AblerAuthError
from custom_components.sportabler.config_flow import (
    SportablerConfigFlow,
    SportablerOptionsFlow,
)


async def test_options_offer_manual_and_hourly_or_slower():
    flow = SportablerOptionsFlow()
    flow.handler = "entry"
    flow.hass = SimpleNamespace(config_entries=Mock())
    flow.hass.config_entries.async_get_known_entry.return_value = SimpleNamespace(
        options={}
    )
    form = await flow.async_step_init()
    schema = form["data_schema"]
    assert schema({}) == {"scan_interval_minutes": 60}
    assert schema({"scan_interval_minutes": 0}) == {"scan_interval_minutes": 0}
    with pytest.raises(vol.Invalid):
        schema({"scan_interval_minutes": 1})
    result = await flow.async_step_init({"scan_interval_minutes": 0})
    assert result["data"] == {"scan_interval_minutes": 0}


@pytest.mark.parametrize("account", ["expected", "wrong"])
async def test_reauth_checks_account_and_saves_rotated_token(monkeypatch, account):
    flow = SportablerConfigFlow()
    flow.context = {"source": "reauth", "entry_id": "entry"}
    flow.handler = "sportabler"
    entry = SimpleNamespace(unique_id="expected", data={"refresh_token": "old"})
    flow.hass = SimpleNamespace(config_entries=Mock())
    flow.hass.config_entries.async_get_known_entry.return_value = entry

    async def set_unique_id(value):
        flow.context["unique_id"] = value

    flow.async_set_unique_id = set_unique_id
    flow.async_update_reload_and_abort = Mock(
        return_value={"type": "abort", "reason": "reauth_successful"}
    )
    client = SimpleNamespace(
        refresh_token="rotated", async_get_me=AsyncMock(return_value={"id": account})
    )
    monkeypatch.setattr(
        "custom_components.sportabler.config_flow.AblerApiClient",
        Mock(return_value=client),
    )
    if account == "wrong":
        with pytest.raises(AbortFlow) as error:
            await flow.async_step_token({"refresh_token": "new"})
        assert error.value.reason == "wrong_account"
        flow.async_update_reload_and_abort.assert_not_called()
    else:
        await flow.async_step_token({"refresh_token": "new"})
        flow.async_update_reload_and_abort.assert_called_once_with(
            entry, data_updates={"refresh_token": "rotated"}
        )


async def setup_service(monkeypatch):
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_update_entry=Mock(),
        ),
        services=Mock(),
    )
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(sportabler.aiohttp, "ClientSession", Mock(return_value=session))
    monkeypatch.setattr(
        sportabler, "async_track_time_interval", Mock(return_value=Mock())
    )
    coordinators = []
    for index, child in enumerate(["first-child", "second-child"]):
        entry = SimpleNamespace(
            entry_id=str(index),
            data={"refresh_token": "token"},
            async_on_unload=Mock(),
            async_start_reauth=Mock(),
        )
        coordinator = SimpleNamespace(
            data={"children": {child: {}}},
            entry=entry,
            async_config_entry_first_refresh=AsyncMock(),
            async_set_attendance=AsyncMock(),
            async_update_listeners=Mock(),
        )
        coordinators.append(coordinator)
        monkeypatch.setattr(
            sportabler, "SportablerCoordinator", Mock(return_value=coordinator)
        )
        await sportabler.async_setup_entry(hass, entry)
    handler = hass.services.async_register.call_args.args[2]
    return hass, coordinators, handler


async def test_attendance_routes_to_matching_account(monkeypatch):
    hass, coordinators, handler = await setup_service(monkeypatch)
    await handler(
        SimpleNamespace(
            data={"child_id": "second-child", "event_id": "event", "status": "G"}
        )
    )
    coordinators[0].async_set_attendance.assert_not_awaited()
    coordinators[1].async_set_attendance.assert_awaited_once_with(
        "second-child", "event", "G"
    )


@pytest.mark.parametrize("ambiguous", [False, True])
async def test_unmatched_or_ambiguous_account_never_sends(monkeypatch, ambiguous):
    hass, coordinators, handler = await setup_service(monkeypatch)
    if ambiguous:
        coordinators[0].data["children"]["second-child"] = {}
    with pytest.raises(HomeAssistantError):
        await handler(
            SimpleNamespace(
                data={
                    "child_id": "second-child" if ambiguous else "unknown",
                    "event_id": "event",
                    "status": "G",
                }
            )
        )
    for coordinator in coordinators:
        coordinator.async_set_attendance.assert_not_awaited()


async def test_attendance_auth_failure_starts_reauth(monkeypatch):
    hass, coordinators, handler = await setup_service(monkeypatch)
    coordinators[1].async_set_attendance.side_effect = AblerAuthError("expired")
    with pytest.raises(HomeAssistantError):
        await handler(
            SimpleNamespace(
                data={"child_id": "second-child", "event_id": "event", "status": "G"}
            )
        )
    coordinators[1].entry.async_start_reauth.assert_called_once_with(hass)


async def test_initial_refresh_failure_closes_session(monkeypatch):
    session = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(sportabler.aiohttp, "ClientSession", Mock(return_value=session))
    coordinator = SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(side_effect=RuntimeError("offline"))
    )
    monkeypatch.setattr(
        sportabler, "SportablerCoordinator", Mock(return_value=coordinator)
    )
    with pytest.raises(RuntimeError):
        await sportabler.async_setup_entry(
            SimpleNamespace(), SimpleNamespace(data={"refresh_token": "token"})
        )
    session.close.assert_awaited_once()
