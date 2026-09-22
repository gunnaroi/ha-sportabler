"""Message snapshot entities make no network calls until explicitly refreshed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.sportabler.api import AblerApiError
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
        async_get_conversations=AsyncMock(
            return_value={
                "items": [{"id": "conversation-1", "name": "Team", "unreadCount": 2}],
                "page_info": PAGE,
            }
        ),
    )
    hass = SimpleNamespace(
        data={
            "sportabler": {
                entry.entry_id: {
                    "client": client,
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
    hass, entry, client = fixtures()
    feed = SportablerFeedSensor(entry, client)
    inbox = SportablerConversationsSensor(entry, client)
    assert feed.native_value is None
    assert inbox.native_value is None
    await feed.async_update()
    assert feed.native_value == "42"
    assert feed.extra_state_attributes["body"] == "Example announcement"
    client.async_get_conversations.assert_not_awaited()
    await inbox.async_update()
    assert inbox.native_value == 1
    assert inbox.extra_state_attributes["conversations"] == [
        {"id": "conversation-1", "name": "Team", "unread_count": 2}
    ]
    client.async_get_news_feed.assert_awaited_once()


async def test_failed_refresh_preserves_previous_snapshot():
    hass, entry, client = fixtures()
    feed = SportablerFeedSensor(entry, client)
    await feed.async_update()
    client.async_get_news_feed.side_effect = AblerApiError("cooldown")
    with pytest.raises(HomeAssistantError):
        await feed.async_update()
    assert feed.native_value == "42"
