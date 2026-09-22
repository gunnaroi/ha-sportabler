"""Message sensors fetch only on manual refresh or the daytime inbox timer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.sensor import SensorEntity
from homeassistant.exceptions import HomeAssistantError

from custom_components.sportabler.api import AblerApiError
from custom_components.sportabler.message_archive import MessageArchive
from custom_components.sportabler.sensor import (
    SportablerConversationsSensor,
    SportablerFeedSensor,
    async_setup_entry,
)

PAGE = {"hasNextPage": False, "endCursor": "cursor-1"}


def fixtures():
    entry = SimpleNamespace(
        entry_id="entry-1", title="Sportabler account", async_start_reauth=Mock()
    )
    client = SimpleNamespace(
        async_get_news_feed=AsyncMock(
            return_value={
                "items": [
                    {
                        "id": 42,
                        "body": "Example announcement",
                        "authorProfile": {"name": "Coach"},
                        "createdAt": 123,
                    }
                ],
                "page_info": PAGE,
            }
        ),
        async_get_conversation_messages=AsyncMock(),
        async_get_conversations=AsyncMock(
            return_value={
                "items": [
                    {
                        "id": "conversation-1",
                        "name": "Team",
                        "unreadCount": 2,
                        "messages": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "message-1",
                                        "messageBody": "Practice moved",
                                        "creator": {"displayName": "Coach"},
                                        "createdAt": 123,
                                    }
                                }
                            ]
                        },
                    }
                ],
                "page_info": PAGE,
            }
        ),
    )
    archive = MessageArchive.__new__(MessageArchive)
    archive._store = SimpleNamespace(async_save=AsyncMock())
    archive._conversations = {}
    archive._sync_lock = asyncio.Lock()
    hass = SimpleNamespace(
        data={
            "sportabler": {
                entry.entry_id: {
                    "client": client,
                    "archive": archive,
                    "coordinator": SimpleNamespace(data={"children": {}}),
                }
            }
        }
    )
    return hass, entry, client


async def test_entities_exist_without_message_requests():
    hass, entry, client = fixtures()
    add = Mock()
    await async_setup_entry(hass, entry, add)
    entities = add.call_args.args[0]
    assert len(entities) == 2
    assert {entity.name for entity in entities} == {"Latest feed post", "Conversations"}
    assert all(entity.should_poll is False for entity in entities)
    client.async_get_news_feed.assert_not_awaited()
    client.async_get_conversations.assert_not_awaited()


async def test_manual_refresh_updates_only_selected_entity():
    _hass, entry, client = fixtures()
    feed = SportablerFeedSensor(entry, client)
    inbox = SportablerConversationsSensor(
        entry, client, _hass.data["sportabler"][entry.entry_id]["archive"]
    )
    assert feed.native_value is None
    assert inbox.native_value is None
    await feed.async_update()
    assert feed.native_value == "42"
    assert feed.extra_state_attributes["body"] == "Example announcement"
    client.async_get_conversations.assert_not_awaited()
    await inbox.async_update()
    assert inbox.native_value == 1
    assert inbox.extra_state_attributes["conversations"] == [
        {
            "id": "conversation-1",
            "name": "Team",
            "unread_count": 2,
            "latest_message": {
                "id": "message-1",
                "body": "Practice moved",
                "sender": "Coach",
                "created_at": 123,
            },
            "new_message": False,
            "stored_message_count": 1,
        }
    ]
    assert inbox.extra_state_attributes["new_message_conversation_ids"] == []
    assert inbox.extra_state_attributes["stored_message_count"] == 1
    client.async_get_news_feed.assert_awaited_once()


async def test_failed_refresh_preserves_previous_snapshot():
    _hass, entry, client = fixtures()
    feed = SportablerFeedSensor(entry, client)
    await feed.async_update()
    client.async_get_news_feed.side_effect = AblerApiError("cooldown")
    with pytest.raises(HomeAssistantError):
        await feed.async_update()
    assert feed.native_value == "42"


async def test_conversation_schedule_is_local_daytime_only():
    hass, entry, client = fixtures()
    inbox = SportablerConversationsSensor(
        entry, client, hass.data["sportabler"][entry.entry_id]["archive"]
    )
    inbox.hass = hass
    cancel = Mock()
    with (
        patch.object(SensorEntity, "async_added_to_hass", new_callable=AsyncMock),
        patch(
            "custom_components.sportabler.sensor.async_track_time_change",
            return_value=cancel,
        ) as track,
        patch.object(inbox, "async_schedule_update_ha_state") as schedule,
    ):
        await inbox.async_added_to_hass()
        track.assert_called_once()
        assert track.call_args.kwargs == {
            "hour": range(7, 23),
            "minute": 0,
            "second": 0,
        }
        track.call_args.args[1](None)
        schedule.assert_called_once_with(True)


async def test_new_latest_message_is_detected_after_initial_snapshot():
    _hass, entry, client = fixtures()
    inbox = SportablerConversationsSensor(
        entry, client, _hass.data["sportabler"][entry.entry_id]["archive"]
    )
    await inbox.async_update()
    client.async_get_conversations.return_value["items"][0]["messages"]["edges"][0][
        "node"
    ] = {
        "id": "message-2",
        "messageBody": "New time",
        "creator": {"displayName": "Coach"},
        "createdAt": 456,
    }
    client.async_get_conversation_messages.return_value = {
        "items": [
            client.async_get_conversations.return_value["items"][0]["messages"][
                "edges"
            ][0]["node"],
            {"id": "message-1", "messageBody": "Practice moved", "createdAt": 123},
        ],
        "page_info": PAGE,
    }
    await inbox.async_update()
    assert inbox.extra_state_attributes["new_message_conversation_ids"] == [
        "conversation-1"
    ]
    assert (
        inbox.extra_state_attributes["conversations"][0]["latest_message"]["body"]
        == "New time"
    )
    await inbox.async_update()
    assert inbox.extra_state_attributes["new_message_conversation_ids"] == []


async def test_inbox_sync_reads_all_conversation_pages():
    _hass, entry, client = fixtures()
    archive = _hass.data["sportabler"][entry.entry_id]["archive"]
    first = client.async_get_conversations.return_value
    second = {
        "items": [{"id": "conversation-2", "name": "Other", "unreadCount": 0}],
        "page_info": PAGE,
    }
    client.async_get_conversations.side_effect = [
        {**first, "page_info": {"hasNextPage": True, "endCursor": "next"}},
        second,
    ]
    inbox = SportablerConversationsSensor(entry, client, archive)
    await inbox.async_update()
    assert inbox.native_value == 2
    assert archive.conversation("conversation-2") is not None
    assert client.async_get_conversations.await_args_list[0].args == (30, None)
    assert client.async_get_conversations.await_args_list[1].args == (30, "next")
