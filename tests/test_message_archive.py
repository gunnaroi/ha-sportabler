"""Changed inbox IDs fetch and persist complete new messages without duplicate reads."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.sportabler.message_archive import MessageArchive

PAGE = {"hasNextPage": False, "endCursor": None}


def archive(saved=None):
    result = MessageArchive.__new__(MessageArchive)
    result._conversations = deepcopy(saved or {})
    result._sync_lock = asyncio.Lock()
    result._store = SimpleNamespace(async_save=AsyncMock())
    return result


def inbox(latest_id, body="Latest"):
    return [
        {
            "id": "conversation-1",
            "name": "Team",
            "unreadCount": 1,
            "messages": {
                "edges": [
                    {
                        "node": {
                            "id": latest_id,
                            "messageBody": body,
                            "createdAt": "2026-09-22T12:00:00Z",
                        }
                    }
                ]
            },
        }
    ]


async def test_initial_inbox_snapshot_is_saved_without_history_burst():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    assert await result.async_sync_conversations(client, inbox("m1")) == {}
    client.async_get_conversation_messages.assert_not_awaited()
    assert result.messages("conversation-1")["items"][0]["id"] == "m1"
    result._store.async_save.assert_awaited_once()
    restarted = archive(result._conversations)
    assert await restarted.async_sync_conversations(client, inbox("m1")) == {}
    client.async_get_conversation_messages.assert_not_awaited()


async def test_changed_thread_fetches_and_stores_all_new_messages_once():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    client.async_get_conversation_messages.return_value = {
        "items": [
            {"id": "m3", "messageBody": "Three", "createdAt": "2026-09-22T12:03:00Z"},
            {"id": "m2", "messageBody": "Two", "createdAt": "2026-09-22T12:02:00Z"},
            {"id": "m1", "messageBody": "One", "createdAt": "2026-09-22T12:01:00Z"},
        ],
        "page_info": PAGE,
    }
    assert await result.async_sync_conversations(client, inbox("m3")) == {
        "conversation-1": ["m3", "m2"]
    }
    assert result.message_count == 3
    assert [item["id"] for item in result.messages("conversation-1")["items"]] == [
        "m3",
        "m2",
        "m1",
    ]
    await result.async_sync_conversations(client, inbox("m3"))
    client.async_get_conversation_messages.assert_awaited_once_with(
        "conversation-1", first=30
    )


async def test_stored_messages_paginate_without_network():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    client.async_get_conversation_messages.return_value = {
        "items": [
            {"id": "m2", "createdAt": "2026-09-22T12:02:00Z"},
            {"id": "m1", "createdAt": "2026-09-22T12:01:00Z"},
        ],
        "page_info": PAGE,
    }
    await result.async_sync_conversations(client, inbox("m2"))
    first = result.messages("conversation-1", first=1)
    assert first["page_info"] == {"hasNextPage": True, "endCursor": "m2"}
    second = result.messages("conversation-1", first=1, after="m2")
    assert [item["id"] for item in second["items"]] == ["m1"]
    with pytest.raises(ValueError):
        result.messages("conversation-1", after="missing")


async def test_changed_thread_paginates_until_previous_head():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    client.async_get_conversation_messages.side_effect = [
        {
            "items": [{"id": "m3", "createdAt": "2026-09-22T12:03:00Z"}],
            "page_info": {"hasNextPage": True, "endCursor": "page-2"},
        },
        {
            "items": [
                {"id": "m2", "createdAt": "2026-09-22T12:02:00Z"},
                {"id": "m1", "createdAt": "2026-09-22T12:01:00Z"},
            ],
            "page_info": PAGE,
        },
    ]
    assert await result.async_sync_conversations(client, inbox("m3")) == {
        "conversation-1": ["m3", "m2"]
    }
    assert result.message_count == 3
    assert result.conversation("conversation-1")["pending"] == []
    assert client.async_get_conversation_messages.call_count == 2


async def test_no_new_inbox_id_skips_history_even_after_restart():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    restarted = archive(result._conversations)
    for _ in range(3):
        await restarted.async_sync_conversations(client, inbox("m1"))
    client.async_get_conversation_messages.assert_not_awaited()


async def test_partial_history_resumes_after_restart():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    pages = []
    for index in range(10):
        pages.append(
            {
                "items": [
                    {
                        "id": f"m{100 - index}",
                        "createdAt": f"2026-09-22T12:{59 - index:02d}:00Z",
                    }
                ],
                "page_info": {"hasNextPage": True, "endCursor": f"page-{index + 1}"},
            }
        )
    client.async_get_conversation_messages.side_effect = pages
    changed = await result.async_sync_conversations(client, inbox("m100"))
    assert len(changed["conversation-1"]) == 10
    assert client.async_get_conversation_messages.call_count == 10
    assert result.conversation("conversation-1")["pending"]
    restarted = archive(result._conversations)
    client.async_get_conversation_messages = AsyncMock(
        return_value={
            "items": [{"id": "m1", "createdAt": "2026-09-22T12:01:00Z"}],
            "page_info": PAGE,
        }
    )
    assert await restarted.async_sync_conversations(client, inbox("m100")) == {}
    client.async_get_conversation_messages.assert_awaited_once_with(
        "conversation-1", first=30, after="page-10"
    )
    assert restarted.conversation("conversation-1")["pending"] == []


async def test_partial_work_is_saved_if_later_page_fails():
    result = archive()
    client = SimpleNamespace(async_get_conversation_messages=AsyncMock())
    await result.async_sync_conversations(client, inbox("m1"))
    client.async_get_conversation_messages.side_effect = [
        {
            "items": [{"id": "m2", "createdAt": "2026-09-22T12:02:00Z"}],
            "page_info": {"hasNextPage": True, "endCursor": "next"},
        },
        RuntimeError("offline"),
    ]
    with pytest.raises(RuntimeError):
        await result.async_sync_conversations(client, inbox("m2"))
    assert result.conversation("conversation-1")["pending"] == [
        {"cursor": "next", "stop_id": "m1"}
    ]
    assert result.message_count == 2
    assert result._store.async_save.await_count == 2


def test_archive_uses_private_home_assistant_storage(monkeypatch):
    from unittest.mock import Mock

    store = Mock()
    monkeypatch.setattr("custom_components.sportabler.message_archive.Store", store)
    MessageArchive(SimpleNamespace(), "account-1")
    assert store.call_args.args[1:] == (1, "sportabler_messages_account-1")
    assert store.call_args.kwargs == {"private": True, "atomic_writes": True}
