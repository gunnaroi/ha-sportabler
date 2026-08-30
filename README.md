# Sportabler (Abler) for Home Assistant

Unofficial integration for [Abler/Sportabler](https://abler.io), reverse-engineered
from the app's own network traffic — there is no public API.

## What it gives you

- One **device per child**, with:
  - A `calendar.<child>` entity listing their upcoming events (practices, matches, etc.)
  - A `sensor.<child>_next_activity` entity with the next event's time, location, team, and
    your current attendance response (`going` / `not_going` / `not_responded`) as attributes
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
`refreshToken` cookie captured once from a real login, and the integration keeps it
alive indefinitely from there (every API call rotates it, and it's re-saved to the
config entry automatically).

1. **Settings → Devices & Services → Add Integration → Sportabler (Abler)**
2. The first screen links to Sportabler's own login page — log in there with your
   phone number as usual, then come back and press **Next**
3. With that tab still open, open developer tools → **Application**/**Storage** →
   **Cookies** → `https://www.abler.io`, and copy the value of the `refreshToken`
   cookie (a long JWT string) into the next screen

The token is normally valid for 80 days and silently renews itself on every poll, so
in practice you should not need to repeat this — only if the session gets invalidated
(e.g. you log out everywhere, or don't use it for 80+ days).

## Notes / limitations

- This talks to Sportabler's internal GraphQL API (`www.abler.io/graphql`), which is
  undocumented and can change without notice.
- The schedule query looks back 1 day and ahead 30 days on each poll (every 15 minutes).
- Match-specific fields (opponent, score) aren't pulled in; only the fields common to
  all event types (time, location, team, attendance).
