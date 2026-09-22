"""Sensor platform for Sportabler."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import AblerApiClient, AblerApiError, AblerAuthError
from .const import ATTENDANCE_STATUS_MAP, DOMAIN
from .coordinator import SportablerCoordinator
from .message_archive import MessageArchive


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: SportablerCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    client: AblerApiClient = hass.data[DOMAIN][entry.entry_id]["client"]
    archive: MessageArchive = hass.data[DOMAIN][entry.entry_id]["archive"]
    entities = [
        SportablerNextActivitySensor(coordinator, child_id)
        for child_id in coordinator.data["children"]
    ]
    unread = SportablerUnreadMessagesSensor(entry, archive)
    entities.extend(
        (
            SportablerFeedSensor(entry, client),
            SportablerConversationsSensor(entry, client, archive, unread),
            unread,
        )
    )
    async_add_entities(entities)


class SportablerNextActivitySensor(
    CoordinatorEntity[SportablerCoordinator], SensorEntity
):
    _attr_has_entity_name = True
    _attr_name = "Next activity"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: SportablerCoordinator, child_id: str) -> None:
        super().__init__(coordinator)
        self._child_id = child_id
        self._attr_unique_id = f"{child_id}_next_activity"
        child = coordinator.data["children"][child_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, child_id)},
            name=child.get("displayName"),
            manufacturer="Sportabler",
        )

    def _next_event(self) -> dict | None:
        now = dt_util.utcnow()
        for event in self.coordinator.data["events_by_child"].get(self._child_id, []):
            if event["start"] >= now:
                return event
        return None

    @property
    def native_value(self):
        event = self._next_event()
        return event["start"] if event else None

    @property
    def extra_state_attributes(self):
        event = self._next_event()
        if not event:
            return {}
        return {
            "name": event["name"],
            "end": event["end"],
            "location": event.get("location"),
            "team": event.get("team"),
            "organization": event.get("organization"),
            "status": event.get("status"),
            "attendance_status": ATTENDANCE_STATUS_MAP.get(
                event.get("attendance_status"), event.get("attendance_status")
            ),
            "event_id": event["id"],
        }


class _SportablerManualSensor(SensorEntity):
    """A snapshot sensor fetched only by homeassistant.update_entity."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, client: AblerApiClient) -> None:
        self._entry = entry
        self._client = client
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Sportabler",
        )

    async def _fetch(self, method):
        try:
            return await method()
        except AblerAuthError as err:
            self._entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Sportabler requires re-authentication") from err
        except AblerApiError as err:
            raise HomeAssistantError(f"Sportabler: {err}") from err


class SportablerFeedSensor(_SportablerManualSensor):
    """Latest feed post from an explicitly refreshed first page."""

    _attr_name = "Latest feed post"

    def __init__(self, entry: ConfigEntry, client: AblerApiClient) -> None:
        super().__init__(entry, client)
        self._attr_unique_id = f"{entry.entry_id}_latest_feed_post"

    async def async_update(self) -> None:
        page = await self._fetch(lambda: self._client.async_get_news_feed())
        latest = page["items"][0] if page["items"] else None
        self._attr_native_value = str(latest["id"]) if latest else None
        author = (latest.get("authorProfile") or {}) if latest else {}
        self._attr_extra_state_attributes = {
            "body": latest.get("body") if latest else None,
            "author": author.get("name"),
            "created_at": latest.get("createdAt") if latest else None,
            "posts_on_page": len(page["items"]),
            "has_more": page["page_info"]["hasNextPage"],
        }


class SportablerUnreadMessagesSensor(SensorEntity):
    """IMAP-style unread-message count; updated by the inbox refresh."""

    _attr_has_entity_name = True
    _attr_name = "Unread messages"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, archive: MessageArchive) -> None:
        self._archive = archive
        self._attr_unique_id = f"{entry.entry_id}_unread_messages"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Sportabler",
        )

    @property
    def native_value(self) -> int | None:
        return self._archive.unread_count


class SportablerConversationsSensor(_SportablerManualSensor):
    """Conversation metadata refreshed hourly during the daytime."""

    _attr_name = "Conversations"

    def __init__(
        self,
        entry: ConfigEntry,
        client: AblerApiClient,
        archive: MessageArchive,
        unread_sensor: SportablerUnreadMessagesSensor | None = None,
    ) -> None:
        super().__init__(entry, client)
        self._archive = archive
        self._unread_sensor = unread_sensor
        self._attr_unique_id = f"{entry.entry_id}_conversations"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def refresh_inbox(_now) -> None:
            self.async_schedule_update_ha_state(True)

        self.async_on_remove(
            async_track_time_change(
                self.hass, refresh_inbox, hour=range(7, 23), minute=0, second=0
            )
        )

    async def async_update(self) -> None:
        inbox_items = []
        cursor = None
        seen_cursors = set()
        while True:
            page = await self._fetch(
                lambda cursor=cursor: self._client.async_get_conversations(30, cursor)
            )
            inbox_items.extend(page["items"])
            if not page["page_info"]["hasNextPage"]:
                break
            cursor = page["page_info"]["endCursor"]
            if not cursor or cursor in seen_cursors:
                raise HomeAssistantError(
                    "Sportabler inbox pagination has no new cursor"
                )
            seen_cursors.add(cursor)
        new_ids = await self._fetch(
            lambda: self._archive.async_sync_conversations(self._client, inbox_items)
        )
        conversations = []
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
            conversations.append(
                {
                    "id": conversation_id,
                    "name": item.get("name")
                    or (
                        (item.get("user2") or {}).get("displayName")
                        or (item.get("user1") or {}).get("displayName")
                    ),
                    "unread_count": item.get("unreadCount", 0),
                    "latest_message": (
                        {
                            "id": str(latest["id"]),
                            "body": latest.get("messageBody"),
                            "sender": (latest.get("creator") or {}).get("displayName"),
                            "created_at": latest.get("createdAt"),
                        }
                        if latest and latest.get("id")
                        else None
                    ),
                    "new_message": conversation_id in new_ids,
                    "stored_message_count": len(
                        (self._archive.conversation(conversation_id) or {}).get(
                            "messages", {}
                        )
                    ),
                }
            )
        self._attr_native_value = len(conversations)
        self._attr_extra_state_attributes = {
            "conversations": conversations,
            "new_message_conversation_ids": list(new_ids),
            "stored_message_count": self._archive.message_count,
        }
        if self._unread_sensor and self._unread_sensor.platform is not None:
            self._unread_sensor.async_write_ha_state()
        for conversation_id, message_ids in new_ids.items():
            saved = self._archive.conversation(conversation_id)["messages"]
            for message_id in message_ids:
                message = saved[message_id]
                self.hass.bus.async_fire(
                    "sportabler_message",
                    {
                        "entry_id": self._entry.entry_id,
                        "conversation_id": conversation_id,
                        "message_id": message_id,
                        "sender": (message.get("creator") or {}).get("displayName"),
                        "created_at": message.get("createdAt"),
                    },
                )
