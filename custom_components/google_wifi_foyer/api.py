"""API client for the undocumented Google Wifi Foyer API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import ipaddress
import logging
import time
from typing import Any

import aiohttp
import gpsoauth
import grpc
from requests import RequestException

from homeassistant.core import HomeAssistant

from .const import (
    ACCESSPOINTS_SCOPE,
    FOYER_BASE_URL,
    FOYER_GRPC_TARGET,
    GOOGLE_HOME_APP,
    GOOGLE_HOME_CLIENT_SIG,
    TOKEN_REFRESH_MARGIN_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

GPSOAUTH_TIMEOUT_SECONDS = 30
FOYER_GRPC_TIMEOUT_SECONDS = 30
LOCAL_STATUS_TIMEOUT_SECONDS = 5

_CREATE_SENSITIVE_OPERATION = (
    "/google.wirelessaccess.accesspoints.v2.StationsService/"
    "CreateOperationForListSensitiveInfo"
)
_LIST_SENSITIVE_INFO = (
    "/google.wirelessaccess.accesspoints.v2.StationsService/ListSensitiveInfo"
)


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
            async with asyncio.timeout(GPSOAUTH_TIMEOUT_SECONDS):
                response = await hass.async_add_executor_job(_exchange)
        except (RequestException, asyncio.TimeoutError) as err:
            raise GoogleWifiFoyerConnectionError(
                f"Could not connect to Google authentication: {err}"
            ) from err
        except Exception as err:
            raise GoogleWifiFoyerAuthError(f"Token exchange failed: {err}") from err

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
                async with asyncio.timeout(GPSOAUTH_TIMEOUT_SECONDS):
                    response = await self._hass.async_add_executor_job(_oauth)
            except (RequestException, asyncio.TimeoutError) as err:
                raise GoogleWifiFoyerConnectionError(
                    f"Could not connect to Google authentication: {err}"
                ) from err
            except Exception as err:
                raise GoogleWifiFoyerAuthError(
                    f"Could not refresh Google access token: {err}"
                ) from err

            token = response.get("Auth")
            if not token:
                error = (
                    response.get("Error") or response.get("error") or "unknown error"
                )
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
                            "Foyer authentication failed "
                            f"({response.status}): {text[:300]}"
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
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise GoogleWifiFoyerConnectionError(
                    f"Could not connect to Foyer: {err}"
                ) from err

        raise GoogleWifiFoyerAuthError("Unable to authenticate with Foyer")

    async def _async_put_json(
        self, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Perform an authenticated JSON PUT."""
        token = await self._async_get_access_token()
        url = f"{FOYER_BASE_URL}{path}"

        for attempt in range(2):
            try:
                async with self._session.put(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 401 and attempt == 0:
                        token = await self._async_get_access_token(force_refresh=True)
                        continue
                    if response.status in (401, 403):
                        text = await response.text()
                        raise GoogleWifiFoyerAuthError(
                            "Foyer authentication failed "
                            f"({response.status}): {text[:300]}"
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
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise GoogleWifiFoyerConnectionError(
                    f"Could not connect to Foyer: {err}"
                ) from err

        raise GoogleWifiFoyerAuthError("Unable to authenticate with Foyer")

    async def async_set_family_paused(
        self, group_id: str, family_id: str, paused: bool
    ) -> None:
        """Pause or resume every station in a Family Wi-Fi group."""
        await self._async_put_json(
            f"/v2/groups/{group_id}/stationBlocking?prettyPrint=false",
            {"blocked": paused, "stationSetId": [family_id]},
        )

    async def async_set_guest_network_enabled(
        self, group_id: str, enabled: bool
    ) -> None:
        """Enable or disable the guest wireless network."""
        await self._async_put_json(
            f"/v2/groups/{group_id}/guestNetwork?prettyPrint=false",
            {"enabled": enabled},
        )

    async def async_set_ap_indicator(self, access_point_id: str, intensity: int) -> None:
        """Set an access point's status-light intensity."""
        await self._async_put_json(
            f"/v2/accesspoints/{access_point_id}/lighting?prettyPrint=false",
            {"automatic": False, "intensity": intensity},
        )

    async def async_get_groups(self) -> list[dict[str, Any]]:
        """Return Google Wifi networks available to the account."""
        data = await self._async_get_json("/v2/groups?prettyPrint=false")
        groups = data.get("groups", [])
        if not isinstance(groups, list):
            return []
        return [group for group in groups if isinstance(group, dict)]

    async def async_get_group(self, group_id: str) -> dict[str, Any] | None:
        """Return one Google Wifi network from the groups response."""
        return next(
            (
                group
                for group in await self.async_get_groups()
                if group.get("id") == group_id
            ),
            None,
        )

    async def async_get_stations(self, group_id: str) -> list[dict[str, Any]]:
        """Return stations for one Google Wifi network."""
        data = await self._async_get_json(
            f"/v2/groups/{group_id}/stations?prettyPrint=false"
        )
        stations = data.get("stations", [])
        if not isinstance(stations, list):
            return []
        return [station for station in stations if isinstance(station, dict)]

    async def async_get_local_status(self, host: str) -> dict[str, Any]:
        """Return status data from a Google Wifi access point's local API."""
        try:
            address = ipaddress.ip_address(host)
        except ValueError as err:
            raise GoogleWifiFoyerConnectionError(
                f"Invalid access point IP address: {host}"
            ) from err

        if not (address.is_private or address.is_link_local):
            raise GoogleWifiFoyerConnectionError(
                f"Refusing to query non-local access point address: {host}"
            )

        formatted_host = f"[{address}]" if address.version == 6 else str(address)
        try:
            async with self._session.get(
                f"http://{formatted_host}/api/v1/status",
                timeout=aiohttp.ClientTimeout(total=LOCAL_STATUS_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise GoogleWifiFoyerConnectionError(
                f"Could not fetch local status from {host}: {err}"
            ) from err

        if not isinstance(data, dict):
            raise GoogleWifiFoyerConnectionError(
                f"Access point {host} returned an unexpected response"
            )
        return data

    async def async_get_sensitive_info(
        self, group_id: str, station_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """Return optional MAC and IPv6 information keyed by station ID."""
        if not station_ids:
            return {}

        create_request = _encode_string_field(1, group_id) + b"".join(
            _encode_string_field(2, station_id) for station_id in station_ids
        )
        create_response = await self._async_grpc_unary(
            _CREATE_SENSITIVE_OPERATION, create_request
        )

        operation = _first_length_delimited(create_response, 1)
        if operation is None:
            raise GoogleWifiFoyerConnectionError(
                "Foyer did not return a sensitive-info operation"
            )
        operation_id = _first_string(operation, 1)
        if not operation_id:
            raise GoogleWifiFoyerConnectionError(
                "Foyer returned a sensitive-info operation without an ID"
            )

        response = await self._async_grpc_unary(
            _LIST_SENSITIVE_INFO,
            _encode_string_field(1, operation_id),
        )

        sensitive_info: dict[str, dict[str, Any]] = {}
        for record in _length_delimited_fields(response, 2):
            station_id = _first_string(record, 1)
            if not station_id:
                continue
            mac_address = _first_string(record, 2)
            ipv6_addresses = [
                value.decode("utf-8")
                for value in _length_delimited_fields(record, 3)
                if value
            ]
            sensitive_info[station_id] = {
                "macAddress": mac_address,
                "ipv6Addresses": ipv6_addresses,
            }

        return sensitive_info

    async def _async_grpc_unary(self, path: str, request: bytes) -> bytes:
        """Perform an authenticated unary Foyer gRPC request."""
        token = await self._async_get_access_token()

        for attempt in range(2):
            channel = grpc.aio.secure_channel(
                FOYER_GRPC_TARGET, grpc.ssl_channel_credentials()
            )
            try:
                call = channel.unary_unary(
                    path,
                    request_serializer=lambda value: value,
                    response_deserializer=lambda value: value,
                )
                return await call(
                    request,
                    metadata=(("authorization", f"Bearer {token}"),),
                    timeout=FOYER_GRPC_TIMEOUT_SECONDS,
                )
            except grpc.aio.AioRpcError as err:
                if (
                    err.code()
                    in (
                        grpc.StatusCode.UNAUTHENTICATED,
                        grpc.StatusCode.PERMISSION_DENIED,
                    )
                    and attempt == 0
                ):
                    token = await self._async_get_access_token(force_refresh=True)
                    continue
                if err.code() in (
                    grpc.StatusCode.UNAUTHENTICATED,
                    grpc.StatusCode.PERMISSION_DENIED,
                ):
                    raise GoogleWifiFoyerAuthError(
                        f"Foyer gRPC authentication failed: {err.details()}"
                    ) from err
                raise GoogleWifiFoyerConnectionError(
                    f"Foyer gRPC request failed ({err.code().name}): {err.details()}"
                ) from err
            finally:
                await channel.close()

        raise GoogleWifiFoyerAuthError("Unable to authenticate with Foyer gRPC")


def _encode_varint(value: int) -> bytes:
    """Encode a non-negative protobuf varint."""
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _encode_string_field(field_number: int, value: str) -> bytes:
    """Encode a length-delimited protobuf string field."""
    payload = value.encode("utf-8")
    return (
        _encode_varint((field_number << 3) | 2) + _encode_varint(len(payload)) + payload
    )


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a protobuf varint and return its value and next offset."""
    value = 0
    shift = 0
    while offset < len(data) and shift < 64:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("Invalid protobuf varint")


def _length_delimited_fields(data: bytes, wanted_field: int) -> list[bytes]:
    """Extract occurrences of one length-delimited protobuf field."""
    values: list[bytes] = []
    offset = 0
    while offset < len(data):
        tag, offset = _decode_varint(data, offset)
        field_number = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:
            _, offset = _decode_varint(data, offset)
            continue
        if wire_type == 1:
            offset += 8
            continue
        if wire_type == 2:
            length, offset = _decode_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("Invalid protobuf field length")
            if field_number == wanted_field:
                values.append(data[offset:end])
            offset = end
            continue
        if wire_type == 5:
            offset += 4
            continue
        raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
    return values


def _first_length_delimited(data: bytes, field_number: int) -> bytes | None:
    """Return the first occurrence of a length-delimited field."""
    values = _length_delimited_fields(data, field_number)
    return values[0] if values else None


def _first_string(data: bytes, field_number: int) -> str | None:
    """Return the first non-empty UTF-8 string field."""
    value = _first_length_delimited(data, field_number)
    if not value:
        return None
    return value.decode("utf-8")
