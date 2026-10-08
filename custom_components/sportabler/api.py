"""Minimal GraphQL client for the Sportabler (abler.io) API.

Reverse-engineered from the Abler app's own traffic. There is no public
API or documentation for this service.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from .const import GRAPHQL_URL, OAUTH_TOKEN_URL, POSTS_GRAPHQL_URL
from .message_queries import (
    QUERY_CONVERSATION_MESSAGES,
    QUERY_CONVERSATIONS,
    QUERY_NEWS_FEED,
    QUERY_POST_COMMENTS,
)

MESSAGE_OPERATIONS = {
    "myNewsFeed",
    "getPostComments",
    "conversationMessages",
    "message",
}

_LOGGER = logging.getLogger(__name__)


def _safe_graphql_validation_error(body: Any) -> str | None:
    """Summarize schema errors without retaining server messages or user data."""
    if not isinstance(body, dict) or not isinstance(body.get("errors"), list):
        return None
    for error in body["errors"]:
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            continue
        message = error["message"]
        for pattern, label in (
            (r"Cannot query field ['\"]([A-Za-z_][A-Za-z0-9_]*)", "unknown field"),
            (r"Unknown argument ['\"]([A-Za-z_][A-Za-z0-9_]*)", "unknown argument"),
            (r"Unknown type ['\"]([A-Za-z_][A-Za-z0-9_]*)", "unknown type"),
            (r"Variable ['\"]\$([A-Za-z_][A-Za-z0-9_]*)", "invalid variable"),
            (r"Argument ['\"]([A-Za-z_][A-Za-z0-9_]*)['\"] has invalid value", "invalid argument"),
        ):
            match = re.search(pattern, message)
            if match:
                return f"{label} {match.group(1)}"
    return None

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
query scheduleV2($filter: scheduleV2Filter, $first: Int!, $after: String) {
  scheduleV2(filter: $filter, pagination: {first: $first, after: $after}) {
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
    pageInfo {
      hasNextPage
      endCursor
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
    """Talk to Abler with its rotating refresh-token and short-lived ID cookies.

    Sportabler's login flow is gated by an invisible reCAPTCHA and SMS OTP,
    which can't be driven headlessly from Home Assistant. Instead, the user
    supplies a `refreshToken` cookie captured once from a logged-in browser
    session. GraphQL bootstraps the short-lived `id_token`; near its expiry,
    `/oauth/token` renews both cookies. The callback persists refresh tokens
    immediately, including cookies returned with error responses.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str,
        token_updated: Callable[[], None] | None = None,
    ) -> None:
        self._session = session
        self._id_token: str | None = None
        self._id_token_expires_at: int | None = None
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
                # Expose only the operation and error category. Never include
                # response bodies, cookie values, URLs, or raw exceptions in HA.
                if isinstance(err, aiohttp.ClientResponseError):
                    reason = f"HTTP {err.status}"
                elif isinstance(err, asyncio.TimeoutError):
                    reason = "timeout"
                elif isinstance(err, ValueError):
                    reason = "invalid response"
                else:
                    reason = type(err).__name__
                raise AblerApiError(
                    f"Sportabler {operation_name} failed ({reason}); requests temporarily paused"
                ) from err

    async def _post_locked(
        self, operation_name: str, query: str, variables: dict[str, Any]
    ) -> dict[str, Any]:
        await self._refresh_access_cookie_if_needed()
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
            POSTS_GRAPHQL_URL
            if operation_name in {"myNewsFeed", "getPostComments"}
            else GRAPHQL_URL,
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
            if resp.status == 400:
                try:
                    error_body = await resp.json(content_type=None)
                except (aiohttp.ClientError, ValueError):
                    error_body = None
                detail = _safe_graphql_validation_error(error_body)
                if detail:
                    self._backoff()
                    raise AblerApiError(
                        f"Sportabler {operation_name} rejected ({detail}); requests temporarily paused"
                    )
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
            "myNewsFeed": "myNewsFeed",
            "getPostComments": "getPostComments",
            "conversationMessages": "conversationMessages",
            "message": "message",
        }[operation_name]
        value = data.get(field)
        valid = isinstance(value, dict)
        if valid and operation_name == "me":
            valid = isinstance(value.get("id"), str) and isinstance(
                value.get("children", []), list
            )
        elif valid and operation_name == "scheduleV2":
            valid = isinstance(value.get("page"), list)
        elif valid and operation_name in MESSAGE_OPERATIONS:
            valid = _valid_connection(value)
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
                self._id_token_expires_at = _jwt_exp(value)

    async def _refresh_access_cookie_if_needed(self) -> None:
        """Refresh Abler's short-lived ID cookie before it expires."""
        if (
            self._id_token is None
            or self._id_token_expires_at is None
            or self._id_token_expires_at > time.time() + 60
        ):
            return

        async with self._session.post(
            OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            headers={"cookie": self._cookie_header()},
            timeout=aiohttp.ClientTimeout(total=30),
            allow_redirects=False,
        ) as resp:
            self._update_cookies(resp)
            if self._token_updated:
                self._token_updated()
            if resp.status in (401, 403) or 300 <= resp.status < 400:
                raise AblerAuthError(
                    "Sportabler denied token renewal; re-authentication required"
                )
            if resp.status == 429 or resp.status >= 500:
                self._backoff(resp.headers.get("Retry-After"))
                raise AblerApiError(
                    "Sportabler token renewal failed; requests temporarily paused"
                )
            if resp.status not in (200, 201):
                if 400 <= resp.status < 500:
                    raise AblerAuthError(
                        "Sportabler rejected token renewal; re-authentication required"
                    )
                raise AblerApiError("Unexpected Sportabler token renewal response")

        if self._id_token is None or self._id_token_expires_at is None:
            self._backoff()
            raise AblerApiError("Sportabler token renewal returned no valid ID cookie")

    async def async_get_me(self) -> dict[str, Any]:
        data = await self._post("me", QUERY_ME, {})
        return data["me"]

    async def async_get_schedule(
        self,
        time_after: str,
        time_before: str,
        child_ids: Sequence[str] | None = None,
        first: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch every page per child, then merge shared events by Abler ID."""
        if type(first) is not int or not 1 <= first <= 100:
            raise AblerApiError("Schedule page size must be between 1 and 100")
        if child_ids is not None and not child_ids:
            return []

        participants: Sequence[str | None] = child_ids or [None]
        events: dict[str, dict[str, Any]] = {}
        for child_id in participants:
            if child_id is not None and (not isinstance(child_id, str) or not child_id):
                raise AblerApiError("Child IDs must be nonempty strings")
            after: str | None = None
            seen_cursors: set[str] = set()
            for _ in range(100):
                event_filter: dict[str, Any] = {
                    "timeAfter": time_after,
                    "timeBefore": time_before,
                }
                if child_id is not None:
                    event_filter["participantIds"] = [child_id]
                data = await self._post(
                    "scheduleV2",
                    QUERY_SCHEDULE,
                    {
                        "filter": event_filter,
                        "first": first,
                        "after": after,
                    },
                )
                connection = data["scheduleV2"]
                page = connection["page"]
                page_info = connection.get("pageInfo")
                if not isinstance(page_info, dict) or not isinstance(
                    page_info.get("hasNextPage"), bool
                ):
                    self._backoff()
                    raise AblerApiError("Schedule response omitted pagination data")
                for event in page:
                    if not isinstance(event, dict):
                        self._backoff()
                        raise AblerApiError("Schedule contained a malformed event")
                    event_id = event.get("id") or event.get("_id")
                    if not isinstance(event_id, (str, int)):
                        self._backoff()
                        raise AblerApiError("Schedule event omitted its ID")
                    key = str(event_id)
                    if key not in events:
                        events[key] = event
                        continue
                    existing_players = events[key].get("familyPlayers")
                    if not isinstance(existing_players, list):
                        existing_players = []
                        events[key]["familyPlayers"] = existing_players
                    known_players = set()
                    for participant in existing_players:
                        if not isinstance(participant, dict):
                            continue
                        player = participant.get("player")
                        player_id = player.get("id") if isinstance(player, dict) else None
                        known_players.add(player_id or participant.get("id"))
                    for participant in event.get("familyPlayers") or []:
                        if not isinstance(participant, dict):
                            continue
                        player = participant.get("player")
                        player_id = player.get("id") if isinstance(player, dict) else None
                        participant_key = player_id or participant.get("id")
                        if participant_key not in known_players:
                            existing_players.append(participant)
                            known_players.add(participant_key)
                if not page_info["hasNextPage"]:
                    break
                cursor = page_info.get("endCursor")
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    self._backoff()
                    raise AblerApiError("Schedule pagination cursor did not advance")
                seen_cursors.add(cursor)
                after = cursor
            else:
                self._backoff()
                raise AblerApiError("Schedule exceeded the 100-page safety limit")

        return list(events.values())

    async def async_set_attendance(
        self, event_id: str, player_id: str, status: str
    ) -> dict[str, Any]:
        variables = {"eventId": event_id, "playerId": player_id, "status": status}
        data = await self._post(
            "setPlayerAttendance", MUTATION_SET_ATTENDANCE, variables
        )
        return data["setPlayerAttendanceV3"]

    async def async_get_news_feed(
        self, first: int = 5, after: str | None = None
    ) -> dict:
        _validate_page(first, after)
        data = await self._post(
            "myNewsFeed", QUERY_NEWS_FEED, {"first": first, "after": after}
        )
        return _connection_response(data["myNewsFeed"])

    async def async_get_post_comments(
        self,
        post_id: int,
        first: int = 2,
        after: str | None = None,
    ) -> dict:
        _validate_page(first, after)
        if type(post_id) is not int or post_id < 1:
            raise AblerApiError("A positive post ID is required")
        data = await self._post(
            "getPostComments",
            QUERY_POST_COMMENTS,
            {"postId": post_id, "first": first, "after": after},
        )
        return _connection_response(data["getPostComments"])

    async def async_get_conversations(
        self,
        first: int = 20,
        after: str | None = None,
    ) -> dict:
        """List one page of conversation IDs without fetching message bodies."""
        _validate_page(first, after)
        data = await self._post(
            "message",
            QUERY_CONVERSATIONS,
            {
                "id": None,
                "first": first,
                "cursor": after,
            },
        )
        return _connection_response(data["message"])

    async def async_get_conversation_messages(
        self,
        conversation_id: str,
        first: int = 30,
        after: str | None = None,
    ) -> dict:
        _validate_page(first, after)
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise AblerApiError("A conversation ID is required")
        data = await self._post(
            "conversationMessages",
            QUERY_CONVERSATION_MESSAGES,
            {
                "conversationIds": [conversation_id],
                "pagination": {"first": first, "after": after},
            },
        )
        return _connection_response(data["conversationMessages"])


def _validate_page(first: int, after: str | None) -> None:
    if type(first) is not int or not 1 <= first <= 30:
        raise AblerApiError("Page size must be between 1 and 30")
    if after is not None and (not isinstance(after, str) or not after.strip()):
        raise AblerApiError("Cursor must be a nonempty string or omitted")


def _jwt_exp(token: str) -> int | None:
    """Read exp only to schedule refresh; token authenticity is server-checked."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, TypeError, UnicodeDecodeError, binascii.Error):
        return None
    expiry = claims.get("exp") if isinstance(claims, dict) else None
    return expiry if type(expiry) is int else None


def _valid_connection(value: dict) -> bool:
    edges, page = value.get("edges"), value.get("pageInfo")
    if not isinstance(edges, list) or not isinstance(page, dict):
        return False
    if not all(
        isinstance(edge, dict)
        and isinstance(edge.get("node"), dict)
        and isinstance(edge["node"].get("id"), (str, int))
        for edge in edges
    ):
        return False
    if not all(
        isinstance(page.get(key), bool) for key in ("hasNextPage", "hasPreviousPage")
    ):
        return False
    if not all(
        page.get(key) is None or isinstance(page[key], str)
        for key in ("startCursor", "endCursor")
    ):
        return False
    return not page["hasNextPage"] or bool(page.get("endCursor"))


def _connection_response(connection: dict) -> dict:
    return {
        "items": [edge["node"] for edge in connection["edges"]],
        "page_info": {
            key: connection["pageInfo"].get(key)
            for key in ("hasNextPage", "hasPreviousPage", "startCursor", "endCursor")
        },
    }
