"""Durable, change-aware storage for Abler conversation messages."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import AblerApiClient

# At most ten conversation-history requests per hourly check, even during catch-up.
MAX_MESSAGE_PAGES_PER_SYNC = 10


class MessageArchive:
    """Keep fetched messages by ID in Home Assistant's private local storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(
            hass, 1, f"sportabler_messages_{entry_id}", private=True, atomic_writes=True
        )
        self._conversations: dict[str, dict[str, Any]] = {}
        self._sync_lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("conversations"), dict):
            self._conversations = data["conversations"]

    async def async_save(self) -> None:
        await self._store.async_save({"conversations": self._conversations})

    def conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self._conversations.get(conversation_id)

    def messages(
        self, conversation_id: str, first: int = 30, after: str | None = None
    ) -> dict[str, Any]:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            raise ValueError("Conversation is not stored")
        ordered = sorted(
            conversation.get("messages", {}).values(),
            key=lambda item: item.get("createdAt") or "",
            reverse=True,
        )
        start = 0
        if after is not None:
            start = next(
                (
                    index + 1
                    for index, item in enumerate(ordered)
                    if str(item["id"]) == after
                ),
                -1,
            )
            if start < 0:
                raise ValueError("Unknown stored message cursor")
        items = ordered[start : start + first]
        has_more = start + first < len(ordered)
        return {
            "conversation_id": conversation_id,
            "items": items,
            "page_info": {
                "hasNextPage": has_more,
                "endCursor": str(items[-1]["id"]) if has_more and items else None,
            },
            "stored_count": len(ordered),
            "sync_pending": bool(conversation.get("pending")),
        }

    @property
    def message_count(self) -> int:
        return sum(
            len(item.get("messages", {})) for item in self._conversations.values()
        )

    async def async_sync_conversations(
        self, client: AblerApiClient, inbox_items: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        """Fetch changed threads, deduplicate, and save completed work."""
        async with self._sync_lock:
            return await self._async_sync_locked(client, inbox_items)

    async def _async_sync_locked(
        self, client: AblerApiClient, inbox_items: list[dict[str, Any]]
    ) -> dict[str, list[str]]:
        remaining = MAX_MESSAGE_PAGES_PER_SYNC
        new_ids: dict[str, list[str]] = {}
        dirty = False
        try:
            for item in inbox_items:
                conversation_id = str(item["id"])
                latest = next(
                    (
                        edge.get("node")
                        for edge in (item.get("messages") or {}).get("edges", [])
                        if edge.get("node")
                    ),
                    None,
                )
                latest_id = str(latest["id"]) if latest and latest.get("id") else None
                conversation = self._conversations.get(conversation_id)
                if conversation is None:
                    # First sighting establishes a baseline. Older history is not
                    # silently downloaded in a burst during setup.
                    self._conversations[conversation_id] = {
                        "name": item.get("name"),
                        "unread_count": item.get("unreadCount", 0),
                        "head_id": latest_id,
                        "pending": [],
                        "messages": {latest_id: latest} if latest_id else {},
                    }
                    dirty = True
                    continue
                conversation["name"] = item.get("name")
                conversation["unread_count"] = item.get("unreadCount", 0)
                dirty = True
                if latest_id and latest_id != conversation.get("head_id") and remaining:
                    old_head = conversation.get("head_id")
                    page = await client.async_get_conversation_messages(
                        conversation_id, first=30
                    )
                    remaining -= 1
                    if not page["items"]:
                        continue
                    reached_old = self._merge_page(
                        conversation, page["items"], new_ids, conversation_id, old_head
                    )
                    conversation["head_id"] = latest_id
                    if not reached_old and page["page_info"]["hasNextPage"]:
                        conversation.setdefault("pending", []).insert(
                            0,
                            {
                                "cursor": page["page_info"]["endCursor"],
                                "stop_id": old_head,
                            },
                        )
                pending = conversation.setdefault("pending", [])
                while pending and remaining:
                    task = pending[0]
                    page = await client.async_get_conversation_messages(
                        conversation_id, first=30, after=task["cursor"]
                    )
                    remaining -= 1
                    reached_old = self._merge_page(
                        conversation,
                        page["items"],
                        new_ids,
                        conversation_id,
                        task["stop_id"],
                    )
                    if reached_old or not page["page_info"]["hasNextPage"]:
                        pending.pop(0)
                    else:
                        task["cursor"] = page["page_info"]["endCursor"]
        finally:
            if dirty:
                await self.async_save()
        return new_ids

    @staticmethod
    def _merge_page(
        conversation: dict[str, Any],
        items: list[dict[str, Any]],
        new_ids: dict[str, list[str]],
        conversation_id: str,
        stop_id: str | None,
    ) -> bool:
        for message in items:
            message_id = str(message["id"])
            if message_id == stop_id:
                return True
            if message_id not in conversation["messages"]:
                conversation["messages"][message_id] = message
                new_ids.setdefault(conversation_id, []).append(message_id)
        return False
