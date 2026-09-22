# Sportabler (Abler) for Home Assistant

Unofficial integration for [Abler/Sportabler](https://abler.io), reverse-engineered
from the app's own network traffic — there is no public API.

## What it gives you

- One **device per child**, with:
  - A `calendar.<child>` entity listing their upcoming events (practices, matches, etc.)
  - A `sensor.<child>_next_activity` entity with the next event's time, location, team, and
    your current attendance response (`going` / `not_going` / `not_responded`) as attributes
- Two account-level snapshot sensors: **Latest feed post** (body and author as
  attributes) and **Conversations** (one page of conversation IDs, names, and
  unread counts). They exist immediately after restart and fetch only when
  explicitly refreshed. No message-history sensor or background message polling
  is created.
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

## Feed and conversation reads

Home Assistant Actions provides four **administrator-only, read-only** actions:

- `sportabler.get_feed` returns one page of feed posts (default 5).
- `sportabler.get_post_comments` takes a numeric `post_id` from a feed result and
  returns one page of comments (default 5).
- `sportabler.get_conversations` discovers one page of conversation IDs and
  labels (default 20), without fetching message bodies.
- `sportabler.get_conversation_messages` takes an exact `conversation_id` from
  a `get_conversations` result and
  returns one page of messages (default 30).

To refresh either new sensor, run `homeassistant.update_entity` for that sensor
under **Developer Tools → Actions**. Its initial value is unknown until the
first refresh. Refreshing one sensor makes one Abler request and does not update
the other. The feed body and conversation names are then stored as Home Assistant
state attributes and may be retained by Recorder; use the response actions below
if you do not want that persistence.

These actions return `items` and `page_info`. If `page_info.hasNextPage` is true,
pass `page_info.endCursor` as `after` on the next call. Each action fetches **one
page only**, up to 30 items. Calling an action makes one request; these features
add no scheduled polling, automatic pagination, or message state changes. The
integration does not call Abler's `MarkAsRead` mutation. The conversation-history action returns message bodies only in its response;
there is no conversation-history entity. Automations that save or forward action
responses may retain them elsewhere.

For multiple Sportabler accounts, include `entry_id` to select one; with one
account it is optional. The actions use the same rotating session token,
serialized request handling, and cooldown as the calendar. The requests are
based on observed Abler web-client traffic and have not been validated against
an installed Home Assistant instance or a live Abler session.

Conversation discovery uses the observed `message` inbox-list query. It runs
only when `get_conversations` is called, retrieves a single page, and does not
fetch every conversation's history. `getMessageUnreadCount` only returns a
number; `MarkAsRead` changes read state. Neither is used for discovery. Do not
paste authentication headers or cookies when sharing captures.

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
