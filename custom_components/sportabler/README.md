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

## Setup

Sportabler's login requires an SMS code behind an invisible reCAPTCHA, which can't be
driven headlessly from Home Assistant. Instead, you bootstrap the integration with a
`refreshToken` cookie captured once from a real login, and the integration keeps it
alive indefinitely from there (every API call rotates it, and it's re-saved to the
config entry automatically).

1. In a desktop browser, go to <https://www.abler.io> and log in as you normally would.
2. Open developer tools (F12) → **Application** (Chrome) or **Storage** (Firefox) →
   **Cookies** → `https://www.abler.io`.
3. Copy the value of the `refreshToken` cookie (a long JWT string).
4. In Home Assistant: **Settings → Devices & Services → Add Integration → Sportabler
   (Abler)**, and paste that value in.

The token is normally valid for 80 days and silently renews itself on every poll, so
in practice you should not need to repeat this — only if the session gets invalidated
(e.g. you log out everywhere, or don't use it for 80+ days).

## Notes / limitations

- This talks to Sportabler's internal GraphQL API (`www.abler.io/graphql`), which is
  undocumented and can change without notice.
- The schedule query looks back 1 day and ahead 30 days on each poll (every 15 minutes).
- Match-specific fields (opponent, score) aren't pulled in; only the fields common to
  all event types (time, location, team, attendance).
