"""Minimal GraphQL client for the Sportabler (abler.io) API.

Reverse-engineered from the Abler app's own traffic. There is no public
API or documentation for this service.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from .const import GRAPHQL_URL

_LOGGER = logging.getLogger(__name__)

QUERY_ME = """
query me {
  me {
    id
    displayName
    children {
      id
      displayName
      __typename
    }
    __typename
  }
}
"""

QUERY_SCHEDULE = """
query scheduleV2($filter: scheduleV2Filter, $first: Int!) {
  scheduleV2(filter: $filter, pagination: {first: $first}) {
    page {
      ... on GenericEventInterface {
        _id
        id
        type
        name
        description
        from
        to
        locationAddress
        locationDetails
        status
        ageGroup {
          id
          name
          organization {
            id
            name
            __typename
          }
          __typename
        }
        familyPlayers: participation(segment: FAMILY, roles: [PLAYER]) {
          ... on EventPlayer {
            id
            status
            player {
              id
              displayName
              __typename
            }
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""

MUTATION_SET_ATTENDANCE = """
mutation setPlayerAttendance($eventId: ID!, $playerId: ID!, $status: PLAYER_ATTENDANCE_STATUS!) {
  setPlayerAttendanceV3(eventId: $eventId, playerId: $playerId, status: $status) {
    eventId
    eventPlayer {
      id
      status
      __typename
    }
    __typename
  }
}
"""


class AblerApiError(Exception):
    """Generic Sportabler API error."""


class AblerAuthError(AblerApiError):
    """The refresh token is no longer valid; the user must re-authenticate."""


class AblerApiClient:
    """Talks to www.abler.io/graphql using a rotating refresh-token cookie.

    Sportabler's login flow is gated by an invisible reCAPTCHA and SMS OTP,
    which can't be driven headlessly from Home Assistant. Instead, the user
    supplies a `refreshToken` cookie captured once from a logged-in browser
    session. When the server rotates that cookie (or the short-lived
    `id_token`), the latest value is retained. The callback persists refresh
    tokens immediately, including cookies returned with error responses.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        token_updated: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._id_token: str | None = None
        self.refresh_token = refresh_token
        self._token_updated = token_updated
        self._lock = asyncio.Lock()
        self._blocked = False
        self._retry_at = 0.0
        self._failures = 0

    def _backoff(self, retry_after: str | None = None) -> None:
        self._failures += 1
        delay = min(3600 * 2 ** min(self._failures - 1, 5), 86400)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                try:
                    date = parsedate_to_datetime(retry_after)
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    delay = max(
                        delay, (date - datetime.now(timezone.utc)).total_seconds()
                    )
                except (ValueError, TypeError, OverflowError):
                    pass
        self._retry_at = time.monotonic() + delay

    def _cookie_header(self) -> str:
        cookies = [f"refreshToken={self.refresh_token}"]
        if self._id_token:
            cookies.append(f"id_token={self._id_token}")
        return "; ".join(cookies)

    async def _post(
        self, operation_name: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._lock:
            if self._blocked:
                raise AblerAuthError("Sportabler authentication requires attention")
            if time.monotonic() < self._retry_at:
                raise AblerApiError("Sportabler requests paused after a failed request")
            try:
                return await self._post_locked(operation_name, query, variables)
            except AblerAuthError:
                self._blocked = True
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                self._backoff()
                raise AblerApiError(
                    "Sportabler request failed; requests temporarily paused"
                ) from err

    async def _post_locked(
        self, operation_name: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        headers = {
            "content-type": "application/json",
            "cookie": self._cookie_header(),
        }
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables,
        }
        async with self._session.post(
            GRAPHQL_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
            allow_redirects=False,
        ) as resp:
            self._update_cookies(resp)
            if self._token_updated:
                self._token_updated()
            if resp.status in (401, 403):
                raise AblerAuthError(
                    "Sportabler denied access; re-authentication required"
                )
            if resp.status == 429 or resp.status >= 500:
                self._backoff(resp.headers.get("Retry-After"))
                raise AblerApiError(
                    "Sportabler unavailable or rate limited; requests paused"
                )
            if 300 <= resp.status < 400:
                raise AblerAuthError("Sportabler redirected the request; sign in again")
            resp.raise_for_status()
            body = await resp.json()

        if not isinstance(body, dict):
            self._backoff()
            raise AblerApiError("Unexpected Sportabler response")
        if body.get("errors"):
            errors = body["errors"]
            if isinstance(errors, list) and any(
                isinstance(error, dict)
                and isinstance(error.get("extensions"), dict)
                and error["extensions"].get("code") in ("UNAUTHENTICATED", "FORBIDDEN")
                for error in errors
            ):
                raise AblerAuthError("Sportabler authentication requires attention")
            self._backoff()
            raise AblerApiError("Sportabler rejected the operation; requests paused")
        if not isinstance(body.get("data"), dict):
            self._backoff()
            raise AblerApiError("Missing Sportabler response data")
        data = body["data"]
        field = {
            "me": "me",
            "scheduleV2": "scheduleV2",
            "setPlayerAttendance": "setPlayerAttendanceV3",
        }[operation_name]
        value = data.get(field)
        valid = isinstance(value, dict)
        if valid and operation_name == "me":
            valid = isinstance(value.get("id"), str) and isinstance(
                value.get("children", []), list
            )
        elif valid and operation_name == "scheduleV2":
            valid = isinstance(value.get("page"), list)
        if not valid:
            self._backoff()
            raise AblerApiError("Incomplete Sportabler response; requests paused")
        self._failures = 0
        self._retry_at = 0.0
        return data

    def _update_cookies(self, resp: aiohttp.ClientResponse) -> None:
        for raw_cookie in resp.headers.getall("Set-Cookie", []):
            name_value = raw_cookie.split(";", 1)[0]
            if "=" not in name_value:
                continue
            name, value = name_value.split("=", 1)
            if not value:
                continue
            if name == "refreshToken":
                self.refresh_token = value
            elif name == "id_token":
                self._id_token = value

    async def async_get_me(self) -> dict[str, Any]:
        data = await self._post("me", QUERY_ME, {})
        return data["me"]

    async def async_get_schedule(
        self, time_after: str, time_before: str, first: int = 300
    ) -> list[dict[str, Any]]:
        variables = {
            "filter": {"timeAfter": time_after, "timeBefore": time_before},
            "first": first,
        }
        data = await self._post("scheduleV2", QUERY_SCHEDULE, variables)
        return data["scheduleV2"]["page"]

    async def async_set_attendance(
        self, event_id: str, player_id: str, status: str
    ) -> dict[str, Any]:
        variables = {"eventId": event_id, "playerId": player_id, "status": status}
        data = await self._post(
            "setPlayerAttendance", MUTATION_SET_ATTENDANCE, variables
        )
        return data["setPlayerAttendanceV3"]
