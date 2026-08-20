"""API client for the undocumented Google Wifi Foyer API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any

import aiohttp
import gpsoauth

from homeassistant.core import HomeAssistant

from .const import (
    ACCESSPOINTS_SCOPE,
    FOYER_BASE_URL,
    GOOGLE_HOME_APP,
    GOOGLE_HOME_CLIENT_SIG,
    TOKEN_REFRESH_MARGIN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class GoogleWifiFoyerError(Exception):
    """Base exception."""


class GoogleWifiFoyerAuthError(GoogleWifiFoyerError):
    """Authentication failed."""


class GoogleWifiFoyerConnectionError(GoogleWifiFoyerError):
    """Connection to Foyer failed."""


@dataclass(slots=True)
class FoyerToken:
    """Cached short-lived OAuth token."""

    value: str
    expires_at: float


class GoogleWifiFoyerApi:
    """Small async wrapper around gpsoauth and the Foyer REST API."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        email: str,
        master_token: str,
        android_id: str,
    ) -> None:
        self._hass = hass
        self._session = session
        self.email = email
        self.master_token = master_token
        self.android_id = android_id
        self._token: FoyerToken | None = None
        self._token_lock = asyncio.Lock()

    @classmethod
    async def async_exchange_oauth_token(
        cls,
        hass: HomeAssistant,
        email: str,
        oauth_token: str,
        android_id: str,
    ) -> str:
        """Exchange EmbeddedSetup oauth_token for a reusable master token."""

        def _exchange() -> dict[str, str]:
            return gpsoauth.exchange_token(email, oauth_token, android_id)

        try:
            response = await hass.async_add_executor_job(_exchange)
        except Exception as err:
            raise GoogleWifiFoyerAuthError(
                f"Token exchange failed: {err}"
            ) from err

        master_token = response.get("Token")
        if not master_token:
            error = response.get("Error") or response.get("error") or "unknown error"
            raise GoogleWifiFoyerAuthError(
                f"Google did not return a master token: {error}"
            )

        return master_token

    async def async_validate_auth(self) -> None:
        """Validate the stored master token."""
        await self._async_get_access_token(force_refresh=True)

    async def _async_get_access_token(self, force_refresh: bool = False) -> str:
        now = time.time()

        if (
            not force_refresh
            and self._token is not None
            and self._token.expires_at - TOKEN_REFRESH_MARGIN_SECONDS > now
        ):
            return self._token.value

        async with self._token_lock:
            now = time.time()
            if (
                not force_refresh
                and self._token is not None
                and self._token.expires_at - TOKEN_REFRESH_MARGIN_SECONDS > now
            ):
                return self._token.value

            def _oauth() -> dict[str, str]:
                return gpsoauth.perform_oauth(
                    self.email,
                    self.master_token,
                    self.android_id,
                    service=ACCESSPOINTS_SCOPE,
                    app=GOOGLE_HOME_APP,
                    client_sig=GOOGLE_HOME_CLIENT_SIG,
                )

            try:
                response = await self._hass.async_add_executor_job(_oauth)
            except Exception as err:
                raise GoogleWifiFoyerAuthError(
                    f"Could not refresh Google access token: {err}"
                ) from err

            token = response.get("Auth")
            if not token:
                error = response.get("Error") or response.get("error") or "unknown error"
                raise GoogleWifiFoyerAuthError(
                    f"Google did not return an access token: {error}"
                )

            try:
                lifetime = int(response.get("ExpiresInDurationSec", "3600"))
            except (TypeError, ValueError):
                lifetime = 3600

            self._token = FoyerToken(
                value=token,
                expires_at=time.time() + max(lifetime, 600),
            )
            return token

    async def _async_get_json(self, path: str) -> dict[str, Any]:
        """Perform authenticated GET and return JSON."""
        token = await self._async_get_access_token()
        url = f"{FOYER_BASE_URL}{path}"

        for attempt in range(2):
            try:
                async with self._session.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 401 and attempt == 0:
                        token = await self._async_get_access_token(force_refresh=True)
                        continue

                    if response.status in (401, 403):
                        text = await response.text()
                        raise GoogleWifiFoyerAuthError(
                            f"Foyer authentication failed ({response.status}): {text[:300]}"
                        )

                    if response.status >= 400:
                        text = await response.text()
                        raise GoogleWifiFoyerConnectionError(
                            f"Foyer returned HTTP {response.status}: {text[:300]}"
                        )

                    data = await response.json(content_type=None)
                    if not isinstance(data, dict):
                        raise GoogleWifiFoyerConnectionError(
                            "Foyer returned an unexpected response"
                        )
                    return data

            except GoogleWifiFoyerError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise GoogleWifiFoyerConnectionError(
                    f"Could not connect to Foyer: {err}"
                ) from err

        raise GoogleWifiFoyerAuthError("Unable to authenticate with Foyer")

    async def async_get_groups(self) -> list[dict[str, Any]]:
        """Return Google Wifi networks available to the account."""
        data = await self._async_get_json("/v2/groups?prettyPrint=false")
        groups = data.get("groups", [])
        return groups if isinstance(groups, list) else []

    async def async_get_stations(self, group_id: str) -> list[dict[str, Any]]:
        """Return stations for one Google Wifi network."""
        data = await self._async_get_json(
            f"/v2/groups/{group_id}/stations?prettyPrint=false"
        )
        stations = data.get("stations", [])
        return stations if isinstance(stations, list) else []
