"""Minimal GraphQL client for the Sportabler (abler.io) API.

Reverse-engineered from the Abler app's own traffic. There is no public
API or documentation for this service.
"""
from __future__ import annotations

import logging
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
      firstName
      picture
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
    session. Every API call rotates that cookie (and the short-lived
    `id_token`); the new value is kept on `self.refresh_token` so the caller
    can persist it back into the config entry.
    """

    def __init__(self, session: aiohttp.ClientSession, refresh_token: str) -> None:
        self._session = session
        self._id_token: str | None = None
        self.refresh_token = refresh_token

    def _cookie_header(self) -> str:
        cookies = [f"refreshToken={self.refresh_token}"]
        if self._id_token:
            cookies.append(f"id_token={self._id_token}")
        return "; ".join(cookies)

    async def _post(
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
            GRAPHQL_URL, json=payload, headers=headers
        ) as resp:
            if resp.status == 401:
                raise AblerAuthError("Sportabler session expired; refresh token rejected")
            resp.raise_for_status()
            self._update_cookies(resp)
            body = await resp.json()

        if body.get("errors"):
            raise AblerApiError(str(body["errors"]))
        return body["data"]

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
