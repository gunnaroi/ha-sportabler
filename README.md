# Sportabler (Abler) for Home Assistant

Unofficial integration for [Abler/Sportabler](https://abler.io), reverse-engineered
from the app's own network traffic — there is no public API.

## What it gives you

- One **device per child**, with:
  - A `calendar.<child>` entity listing their upcoming events (practices, matches, etc.)
  - A `sensor.<child>_next_activity` entity with the next event's time, location, team, and
    your current attendance response (`going` / `not_going` / `not_responded`) as attributes
- Three account-level sensors: **Latest feed post** (manual refresh),
  **Conversations**, and **Unread messages**. The Conversations sensor checks the full inbox hourly from
  07:00 through 22:00 in Home Assistant's local timezone. Its attributes show
  each conversation's latest message and the total number of messages stored.
  When the latest message ID changes, the integration fetches that conversation's
  new messages and saves them by ID in Home Assistant's private local storage.
  Unchanged conversations do not trigger history requests. The **Unread messages**
  sensor reports the inbox's unread count, and each newly saved message fires a
  `sportabler_message` event for automations. The first check saves the latest
  message as a baseline; it does not automatically import old history.
- A `sportabler.set_attendance` service to RSVP a child to an event from an automation
  or dashboard button (fields: `child_id`, `event_id`, `status: G|N` — ids are visible in
  the sensor's `event_id` attribute and the device's identifiers)

## Install via HACS

1. In Home Assistant: **HACS → Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/gunnaroi/ha-sportabler`, category **Integration**
3. Find "Sportabler (Abler)" in HACS and install it, then restart Home Assistant

## Setup

Sportabler's login requires an SMS code behind an invisible reCAPTCHA, which can't be
driven headlessly from Home Assistant. Instead, you bootstrap the integration with a
`refreshToken` cookie captured from a real login. When the server rotates session
cookies, the integration saves the latest refresh token immediately. Sessions may
still expire or be revoked; Home Assistant will ask you to re-authenticate.

1. **Settings → Devices & Services → Add Integration → Sportabler (Abler)**
2. The first screen links to Sportabler's own login page — log in there with your
   phone number as usual, then come back and press **Next**
3. With that tab still open, open developer tools → **Application**/**Storage** →
   **Cookies** → `https://www.abler.io`, and copy the value of the `refreshToken`
   cookie (a long JWT string) into the next screen

Do not rely on a fixed token lifetime. If access is rejected, requests stop until
you re-authenticate. Use the same Abler account when replacing the session token.

## Request frequency

In the integration's **Configure** menu, choose hourly (the default), every 3, 6,
or 12 hours, daily, or **Manual only**. Manual mode fetches once at startup/reload;
subsequent updates require `homeassistant.update_entity` targeting one Sportabler
entity. Saving options reloads the integration and fetches once.

Profiles are cached for 24 hours in memory. With continuous uptime, hourly mode
makes roughly 25 requests per day per account, compared with 192 previously.
The daytime Conversations sensor adds 16 inbox checks per day, plus any
pagination needed to list the inbox. A changed conversation triggers a history
request; at most ten history pages are requested per check. Unfinished pages
continue on the next check. The archive survives Home Assistant restarts.
Startup, reloads, manual updates, and attendance submissions add requests.
Less frequent updates mean schedule changes reach Home Assistant later.

- Calendar views use cached events. A local one-minute timer advances time-based
  entity displays without contacting Abler.
- Requests are serialized. Rate limits and server/network errors start a cooldown
  of at least one hour, increasing up to 24 hours after consecutive failures.
  A longer server `Retry-After` is honored (seconds or HTTP date).
- Manual updates and attendance also respect the cooldown. Failed attendance
  writes are never automatically retried; a timeout may have occurred after the
  server accepted the write, so confirm the status before submitting again.
- Authentication/access rejection stops requests and starts re-authentication.
- Attendance uses the child's matching account and the server's returned status
  to update the cache without a full schedule fetch. Ambiguous accounts and
  events absent from the cache are rejected before sending a mutation.
- Profile caches and cooldowns are in memory and reset on restart/reload. Restarting
  repeatedly causes new requests; it is not a way to resolve a rate limit.

## Messages and stored history

The **Conversations** sensor refreshes on startup during daytime and then at the
top of each hour from 07:00 through 22:00, Home Assistant local time. Overnight
starts wait until 07:00. To check immediately, run
`homeassistant.update_entity` targeting that sensor. Its `conversations` attribute
shows names, unread counts, latest-message text, and `stored_message_count`. The
`new_message_conversation_ids` attribute lists conversations with messages saved
since the preceding check. A fresh installation takes its first check as the
baseline. The feed sensor remains manual-only.

New messages are saved in Home Assistant's private `.storage` directory, keyed by
config entry and message ID. Saving them does not mark them read in Abler. The
archive contains all messages fetched since the baseline, including multiple
messages that arrived between hourly checks, subject to the ten-page-per-check
limit. It does not automatically import older history.

To read the saved messages under **Developer Tools → Actions**, call:

```yaml
action: sportabler.get_stored_conversation_messages
data:
  conversation_id: "PASTE_ID_FROM_CONVERSATIONS_SENSOR"
```

This administrator-only action returns up to 30 stored messages under `items`
and makes no Abler request. If `page_info.hasNextPage` is true, pass
`page_info.endCursor` as `after` to get the next saved page. The action
`sportabler.get_conversation_messages` still reads a conversation directly from
Abler on demand; it does not change the archive. Other administrator-only
read-only actions are `sportabler.get_conversations`, `sportabler.get_feed`, and
`sportabler.get_post_comments`. Direct Abler actions return one page per call,
up to 30 items, and do not invoke `MarkAsRead`.

Message text in the Conversations sensor is a Home Assistant state attribute and
may also be retained by Recorder. The archive file is private local storage,
not encrypted; protect your Home Assistant backups and administrator accounts.
This uses Abler's undocumented GraphQL API and has not yet been tested against
a live Home Assistant installation and Abler session.

## Notes / limitations

- This talks to Sportabler's internal GraphQL API (`www.abler.io/graphql`), which is
  undocumented and can change without notice.
- The schedule query looks back 1 day and ahead 30 days on each poll (hourly by default).
- Match-specific fields (opponent, score) aren't pulled in; only the fields common to
  all event types (time, location, team, attendance).

## Development checks

Install `homeassistant`, `pytest`, and `pytest-asyncio` in a virtual environment,
then run `python -m pytest`. Tests use simulated API responses and do not contact
Abler. Live authentication and message behavior still require verification with
the service.
