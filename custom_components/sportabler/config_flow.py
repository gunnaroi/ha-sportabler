"""Config flow for Sportabler (Abler)."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import AblerApiClient, AblerApiError, AblerAuthError
from .const import (
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://abler.io/sign-on/"

STEP_TOKEN_SCHEMA = vol.Schema({vol.Required(CONF_REFRESH_TOKEN): str})


class SportablerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sportabler."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SportablerOptionsFlow()

    async def async_step_reauth(self, entry_data):
        return await self.async_step_token()

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
                    if self.source == config_entries.SOURCE_REAUTH:
                        entry = self._get_reauth_entry()
                        self._abort_if_unique_id_mismatch(reason="wrong_account")
                        return self.async_update_reload_and_abort(
                            entry,
                            data_updates={CONF_REFRESH_TOKEN: client.refresh_token},
                        )
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=me.get("displayName", "Sportabler"),
                        data={CONF_REFRESH_TOKEN: client.refresh_token},
                    )

        return self.async_show_form(
            step_id="token", data_schema=STEP_TOKEN_SCHEMA, errors=errors
        )


class SportablerOptionsFlow(config_entries.OptionsFlowWithReload):
    """Choose automatic polling frequency, or disable scheduled requests."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): vol.In(
                        {
                            0: "Manual only",
                            60: "Every hour",
                            180: "Every 3 hours",
                            360: "Every 6 hours",
                            720: "Every 12 hours",
                            1440: "Daily",
                        }
                    ),
                }
            ),
        )
