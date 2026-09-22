"""Message actions are explicit, bounded, and keep content out of state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from test_requests import Response, client_with

from custom_components import sportabler
from custom_components.sportabler.api import (
    AblerApiError,
    AblerAuthError,
)
from custom_components.sportabler.const import GRAPHQL_URL, POSTS_GRAPHQL_URL

PAGE = {
    "hasNextPage": False,
    "hasPreviousPage": False,
    "startCursor": "cursor-1",
    "endCursor": "cursor-1",
}


def connection(nodes, page=None):
    return {"edges": [{"node": item} for item in nodes], "pageInfo": page or PAGE}


@pytest.mark.parametrize(
    "operation,field,node,expected_url,variables",
    [
        (
            "myNewsFeed",
            "myNewsFeed",
            {"id": 8, "body": "sample post"},
            POSTS_GRAPHQL_URL,
            {"first": 5, "after": None},
        ),
        (
            "getPostComments",
            "getPostComments",
            {"id": 9, "body": "sample comment"},
            POSTS_GRAPHQL_URL,
            {"postId": 8, "first": 2, "after": "older"},
        ),
        (
            "conversationMessages",
            "conversationMessages",
            {"id": "message-1", "messageBody": "sample message"},
            GRAPHQL_URL,
            {
                "conversationIds": ["conversation-1"],
                "pagination": {"first": 30, "after": None},
            },
        ),
    ],
)
async def test_one_bounded_request_per_action(
    operation, field, node, expected_url, variables
):
    client, session = client_with(Response({"data": {field: connection([node])}}))
    if operation == "myNewsFeed":
        result = await client.async_get_news_feed()
    elif operation == "getPostComments":
        result = await client.async_get_post_comments(8, after="older")
    else:
        result = await client.async_get_conversation_messages("conversation-1")
    assert result == {"items": [node], "page_info": PAGE}
    session.post.assert_called_once()
    assert session.post.call_args.args == (expected_url,)
    assert session.post.call_args.kwargs["json"]["operationName"] == operation
    assert session.post.call_args.kwargs["json"]["variables"] == variables


async def test_cursor_is_passed_without_auto_paging():
    next_page = {**PAGE, "hasNextPage": True, "endCursor": "cursor-2"}
    client, session = client_with(
        Response(
            {
                "data": {
                    "conversationMessages": connection([{"id": "message-2"}], next_page)
                }
            }
        )
    )
    result = await client.async_get_conversation_messages(
        "conversation-1", first=1, after="cursor-1"
    )
    assert result["page_info"]["endCursor"] == "cursor-2"
    assert session.post.call_count == 1
    assert session.post.call_args.kwargs["json"]["variables"]["pagination"] == {
        "first": 1,
        "after": "cursor-1",
    }


@pytest.mark.parametrize("size", [0, 31, True])
async def test_invalid_page_size_never_requests(size):
    client, session = client_with()
    with pytest.raises(AblerApiError):
        await client.async_get_conversation_messages("conversation-1", first=size)
    session.post.assert_not_called()


async def test_malformed_connection_pauses_requests():
    client, session = client_with(
        Response(
            {
                "data": {
                    "conversationMessages": {
                        "edges": [],
                        "pageInfo": {"hasNextPage": True},
                    }
                }
            }
        )
    )
    for _ in range(2):
        with pytest.raises(AblerApiError):
            await client.async_get_conversation_messages("conversation-1")
    assert session.post.call_count == 1


async def test_actions_require_admin_and_select_one_account(monkeypatch):
    registrations = {}

    def register(hass, domain, name, handler, schema, *, supports_response):
        assert supports_response is SupportsResponse.ONLY
        registrations[name] = (handler, schema)

    monkeypatch.setattr(sportabler, "async_register_admin_service", register)
    one = SimpleNamespace(
        async_get_news_feed=AsyncMock(return_value={"items": [], "page_info": PAGE}),
        async_get_post_comments=AsyncMock(
            return_value={"items": [], "page_info": PAGE}
        ),
        async_get_conversation_messages=AsyncMock(
            return_value={"items": [], "page_info": PAGE}
        ),
    )
    two = SimpleNamespace(
        **{
            name: AsyncMock()
            for name in (
                "async_get_news_feed",
                "async_get_post_comments",
                "async_get_conversation_messages",
            )
        }
    )
    entry1 = SimpleNamespace(
        client=one,
        coordinator=SimpleNamespace(entry=SimpleNamespace(async_start_reauth=Mock())),
    )
    entry2 = SimpleNamespace(
        client=two,
        coordinator=SimpleNamespace(entry=SimpleNamespace(async_start_reauth=Mock())),
    )
    hass = SimpleNamespace(data={"sportabler": {"one": vars(entry1)}})
    assert await sportabler.async_setup(hass, {}) is True
    assert set(registrations) == {
        "get_feed",
        "get_post_comments",
        "get_conversation_messages",
    }
    feed, schema = registrations["get_feed"]
    await feed(SimpleNamespace(data=schema({})))
    one.async_get_news_feed.assert_awaited_once_with(5, None)
    hass.data["sportabler"]["two"] = vars(entry2)
    with pytest.raises(HomeAssistantError):
        await feed(SimpleNamespace(data=schema({})))
    await feed(SimpleNamespace(data=schema({"entry_id": "two"})))
    two.async_get_news_feed.assert_awaited_once_with(5, None)
    conversation, schema = registrations["get_conversation_messages"]
    await conversation(
        SimpleNamespace(
            data=schema({"entry_id": "one", "conversation_id": "conversation-1"})
        )
    )
    one.async_get_conversation_messages.assert_awaited_once_with(
        "conversation-1", 30, None
    )


async def test_auth_failure_requests_reauth_without_message_mutation(monkeypatch):
    registrations = {}
    monkeypatch.setattr(
        sportabler,
        "async_register_admin_service",
        lambda hass, domain, name, handler, schema, **kw: registrations.setdefault(
            name, (handler, schema)
        ),
    )
    entry = SimpleNamespace(async_start_reauth=Mock())
    client = SimpleNamespace(
        async_get_conversation_messages=AsyncMock(side_effect=AblerAuthError("expired"))
    )
    hass = SimpleNamespace(
        data={
            "sportabler": {
                "one": {"client": client, "coordinator": SimpleNamespace(entry=entry)}
            }
        }
    )
    await sportabler.async_setup(hass, {})
    handler, schema = registrations["get_conversation_messages"]
    with pytest.raises(HomeAssistantError):
        await handler(
            SimpleNamespace(data=schema({"conversation_id": "conversation-1"}))
        )
    entry.async_start_reauth.assert_called_once_with(hass)
