"""Config flow for Google Wifi Foyer."""

from __future__ import annotations

import secrets
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GoogleWifiFoyerApi,
    GoogleWifiFoyerAuthError,
    GoogleWifiFoyerConnectionError,
)
from .const import (
    CONF_ANDROID_ID,
    CONF_GROUP_ID,
    CONF_MASTER_TOKEN,
    CONF_NETWORK_NAME,
    DOMAIN,
)


def _network_name(group: dict[str, Any]) -> str:
    """Extract a useful network name."""
    settings = group.get("groupSettings", {})
    wlan = settings.get("wlanSettings", {})
    ssid = wlan.get("privateSsid")
    if isinstance(ssid, str) and ssid:
        return ssid
    return "Unnamed Google Wifi network"


class GoogleWifiFoyerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a Google Wifi Foyer config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str | None = None
        self._master_token: str | None = None
        self._android_id: str | None = None
        self._groups: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Authenticate with Google using an EmbeddedSetup oauth_token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip().lower()
            oauth_token = user_input["oauth_token"].strip()
            android_id = secrets.token_hex(8)

            try:
                master_token = await GoogleWifiFoyerApi.async_exchange_oauth_token(
                    self.hass,
                    email,
                    oauth_token,
                    android_id,
                )

                api = GoogleWifiFoyerApi(
                    hass=self.hass,
                    session=async_get_clientsession(self.hass),
                    email=email,
                    master_token=master_token,
                    android_id=android_id,
                )
                groups = await api.async_get_groups()

                if not groups:
                    errors["base"] = "no_networks"
                else:
                    self._email = email
                    self._master_token = master_token
                    self._android_id = android_id
                    self._groups = groups
                    return await self.async_step_network()

            except GoogleWifiFoyerAuthError:
                errors["base"] = "invalid_auth"
            except GoogleWifiFoyerConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required("oauth_token"): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_network(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select which Google Wifi network this config entry represents."""
        if not self._groups or self._email is None or self._master_token is None:
            return self.async_abort(reason="reauth_required")

        group_by_id = {
            group["id"]: group
            for group in self._groups
            if isinstance(group.get("id"), str)
        }
        options = [
            selector.SelectOptionDict(value=group_id, label=_network_name(group))
            for group_id, group in group_by_id.items()
        ]

        if user_input is not None:
            group_id = user_input[CONF_GROUP_ID]
            group = group_by_id[group_id]
            network_name = _network_name(group)

            await self.async_set_unique_id(group_id)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=network_name,
                data={
                    CONF_EMAIL: self._email,
                    CONF_MASTER_TOKEN: self._master_token,
                    CONF_ANDROID_ID: self._android_id,
                    CONF_GROUP_ID: group_id,
                    CONF_NETWORK_NAME: network_name,
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_GROUP_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="network",
            data_schema=schema,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Start reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reauthenticate using a fresh EmbeddedSetup oauth_token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}

        if user_input is not None and "oauth_token" in user_input:
            oauth_token = user_input["oauth_token"].strip()
            android_id = secrets.token_hex(8)

            try:
                master_token = await GoogleWifiFoyerApi.async_exchange_oauth_token(
                    self.hass,
                    entry.data[CONF_EMAIL],
                    oauth_token,
                    android_id,
                )

                api = GoogleWifiFoyerApi(
                    hass=self.hass,
                    session=async_get_clientsession(self.hass),
                    email=entry.data[CONF_EMAIL],
                    master_token=master_token,
                    android_id=android_id,
                )
                await api.async_validate_auth()

            except GoogleWifiFoyerAuthError:
                errors["base"] = "invalid_auth"
            except GoogleWifiFoyerConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                new_data = {
                    **entry.data,
                    CONF_MASTER_TOKEN: master_token,
                    CONF_ANDROID_ID: android_id,
                }
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_MASTER_TOKEN: master_token,
                        CONF_ANDROID_ID: android_id,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {vol.Required("oauth_token"): str}
            ),
            errors=errors,
            description_placeholders={
                "email": entry.data[CONF_EMAIL],
            },
        )
