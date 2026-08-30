"""Config flow for Sportabler (Abler)."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .api import AblerApiClient, AblerApiError, AblerAuthError
from .const import CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://abler.io/sign-on/"

STEP_TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str})


class SportablerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sportabler."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show a link to Sportabler's own login page, then move on to the token step.

        Sportabler's login is gated by SMS + an invisible reCAPTCHA bound to
        their domain, so it can't be driven from here directly - the user
        completes it themselves on abler.io and comes back with the resulting
        `refreshToken` cookie.
        """
        if user_input is not None:
            return await self.async_step_token()

        return self.async_show_form(step_id="user")

    async def async_step_token(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_REFRESH_TOKEN].strip()
            async with aiohttp.ClientSession(
                cookie_jar=aiohttp.DummyCookieJar()
            ) as session:
                client = AblerApiClient(session, token)
                try:
                    me = await client.async_get_me()
                except AblerAuthError:
                    errors["base"] = "invalid_auth"
                except AblerApiError:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(me["id"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=me.get("displayName", "Sportabler"),
                        data={CONF_REFRESH_TOKEN: client.refresh_token},
                    )

        return self.async_show_form(
            step_id="token", data_schema=STEP_TOKEN_SCHEMA, errors=errors
        )
