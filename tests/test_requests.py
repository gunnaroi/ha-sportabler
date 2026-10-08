"""Offline behavioral tests for request volume and error handling."""

import asyncio
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from multidict import CIMultiDict

from custom_components.sportabler.api import (
    AblerApiClient,
    AblerApiError,
    AblerAuthError,
)
from custom_components.sportabler.coordinator import SportablerCoordinator


class Response:
    def __init__(self, body=None, status=200, headers=()):
        self.status = status
        self.headers = CIMultiDict(headers)
        self.body = body if body is not None else {"data": {"me": {"id": "account"}}}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(Mock(), (), status=self.status)

    async def json(self, **_kwargs):
        return self.body


def client_with(*responses, callback=None):
    session = Mock()
    session.post.side_effect = responses
    return AblerApiClient(session, "initial", callback), session


@pytest.mark.parametrize("status", [401, 403, 302])
async def test_auth_blocks_all_later_requests(status):
    client, session = client_with(Response(status=status))
    with pytest.raises(AblerAuthError):
        await client.async_get_me()
    with pytest.raises(AblerAuthError):
        await client.async_set_attendance("event", "child", "G")
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["allow_redirects"] is False


async def test_graphql_auth_blocks_retries():
    client, session = client_with(
        Response({"errors": [{"extensions": {"code": "UNAUTHENTICATED"}}]})
    )
    for _ in range(2):
        with pytest.raises(AblerAuthError):
            await client.async_get_me()
    assert session.post.call_count == 1


@pytest.mark.parametrize(
    "retry_after",
    ["7200", format_datetime(datetime.now(timezone.utc) + timedelta(hours=3))],
)
async def test_retry_after_blocks_reads_and_writes(retry_after):
    client, session = client_with(
        Response(status=429, headers=[("Retry-After", retry_after)])
    )
    for operation in (
        client.async_get_me,
        lambda: client.async_set_attendance("e", "c", "G"),
    ):
        with pytest.raises(AblerApiError):
            await operation()
    assert client._retry_at - time.monotonic() > 7100
    assert session.post.call_count == 1


async def test_backoff_increases_and_recovers():
    client, session = client_with(
        Response(status=503), Response(status=503), Response()
    )
    with pytest.raises(AblerApiError):
        await client.async_get_me()
    assert 3500 < client._retry_at - time.monotonic() <= 3600
    client._retry_at = 0
    with pytest.raises(AblerApiError):
        await client.async_get_me()
    assert 7100 < client._retry_at - time.monotonic() <= 7200
    client._retry_at = 0
    await client.async_get_me()
    assert client._failures == 0


async def test_timeout_does_not_retry_mutation():
    client, session = client_with(asyncio.TimeoutError())
    for _ in range(2):
        with pytest.raises(AblerApiError):
            await client.async_set_attendance("e", "c", "G")
    assert session.post.call_count == 1


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (Response(status=400), "HTTP 400"),
        (asyncio.TimeoutError(), "timeout"),
        (ValueError("private response body"), "invalid response"),
    ],
)
async def test_request_failure_reports_safe_diagnostic(response, reason):
    client, _ = client_with(response)
    with pytest.raises(AblerApiError, match=f"me failed \\({reason}\\)") as caught:
        await client.async_get_me()
    assert "private response body" not in str(caught.value)


async def test_graphql_validation_diagnostic_exposes_only_field_name():
    client, _ = client_with(Response(
        {"errors": [{"message": 'Cannot query field "legacyField" on type "Event" for user Private Person'}]},
        status=400,
    ))
    with pytest.raises(AblerApiError, match="unknown field legacyField") as caught:
        await client.async_get_schedule("2026-10-08", "2026-11-08", ["child"])
    assert "Private Person" not in str(caught.value)


async def test_rotated_token_persisted_even_on_error():
    callback = Mock()
    client, session = client_with(
        Response(status=503, headers=[("Set-Cookie", "refreshToken=rotated; Secure")]),
        callback=callback,
    )
    with pytest.raises(AblerApiError):
        await client.async_get_me()
    assert client.refresh_token == "rotated"
    callback.assert_called_once()


async def test_concurrent_calls_use_latest_rotated_cookie():
    entered = asyncio.Event()
    release = asyncio.Event()

    class DelayedResponse(Response):
        async def __aenter__(self):
            entered.set()
            await release.wait()
            return self

    client, session = client_with(
        DelayedResponse(headers=[("Set-Cookie", "refreshToken=rotated; Secure")]),
        Response(),
    )
    first = asyncio.create_task(client.async_get_me())
    await entered.wait()
    second = asyncio.create_task(client.async_get_me())
    await asyncio.sleep(0)
    assert session.post.call_count == 1
    release.set()
    await asyncio.gather(first, second)
    assert session.post.call_args.kwargs["headers"]["cookie"] == "refreshToken=rotated"


def make_coordinator(tmp_path, minutes=60):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain="sportabler",
        title="Test",
        source="user",
        unique_id="account",
        data={"refresh_token": "initial"},
        options={"scan_interval_minutes": minutes},
        discovery_keys={},
        subentries_data=[],
    )
    client = SimpleNamespace(
        refresh_token="initial",
        async_get_me=AsyncMock(
            return_value={"id": "account", "children": [{"id": "child"}]}
        ),
        async_get_schedule=AsyncMock(return_value=[]),
        async_set_attendance=AsyncMock(
            return_value={"eventId": "event", "eventPlayer": {"status": "G"}}
        ),
    )
    return SportablerCoordinator(hass, entry, client)


async def test_profile_cached_for_day_and_manual_disables_timer(tmp_path):
    coordinator = make_coordinator(tmp_path, 0)
    assert coordinator.update_interval is None
    await coordinator._async_update_data()
    await coordinator._async_update_data()
    assert coordinator.client.async_get_me.await_count == 1
    assert coordinator.client.async_get_schedule.await_count == 2
    coordinator._profile_updated -= 86401
    await coordinator._async_update_data()
    assert coordinator.client.async_get_me.await_count == 2


async def test_hourly_request_budget(tmp_path):
    coordinator = make_coordinator(tmp_path)
    assert coordinator.update_interval == timedelta(hours=1)
    for _ in range(24):
        await coordinator._async_update_data()
    assert coordinator.client.async_get_me.await_count == 1
    assert coordinator.client.async_get_schedule.await_count == 24


async def test_auth_error_signals_home_assistant(tmp_path):
    coordinator = make_coordinator(tmp_path)
    coordinator.client.async_get_me.side_effect = AblerAuthError("expired")
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()
    coordinator.client.async_get_schedule.assert_not_awaited()


async def test_attendance_updates_cache_without_fetch(tmp_path):
    coordinator = make_coordinator(tmp_path)
    original = {"id": "event", "attendance_status": "N"}
    coordinator.data = {
        "children": {"child": {}},
        "events_by_child": {"child": [original]},
    }
    await coordinator.async_set_attendance("child", "event", "G")
    assert coordinator.data["events_by_child"]["child"][0]["attendance_status"] == "G"
    assert original["attendance_status"] == "N"
    coordinator.client.async_get_me.assert_not_awaited()
    coordinator.client.async_get_schedule.assert_not_awaited()


async def test_unknown_event_never_mutates(tmp_path):
    coordinator = make_coordinator(tmp_path)
    coordinator.data = {"children": {"child": {}}, "events_by_child": {"child": []}}
    with pytest.raises(AblerApiError):
        await coordinator.async_set_attendance("child", "unknown", "G")
    coordinator.client.async_set_attendance.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {"data": {}},
        {"data": {"me": None}},
        {"data": {"me": {}}},
        {"errors": [{"message": "private server details"}]},
    ],
)
async def test_invalid_responses_pause_without_logging_payload(body):
    client, session = client_with(Response(body))
    for _ in range(2):
        with pytest.raises(AblerApiError) as error:
            await client.async_get_me()
        assert "private server details" not in str(error.value)
    assert session.post.call_count == 1


async def test_attendance_does_not_replace_cache_on_unconfirmed_result(tmp_path):
    coordinator = make_coordinator(tmp_path)
    coordinator.data = {
        "children": {"child": {}},
        "events_by_child": {"child": [{"id": "event", "attendance_status": "N"}]},
    }
    coordinator.client.async_set_attendance.return_value = {
        "eventId": "other",
        "eventPlayer": {"status": "G"},
    }
    with pytest.raises(AblerApiError):
        await coordinator.async_set_attendance("child", "event", "G")
    assert coordinator.data["events_by_child"]["child"][0]["attendance_status"] == "N"
