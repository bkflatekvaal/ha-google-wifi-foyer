"""Data coordinator for Google Wifi Foyer."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GoogleWifiFoyerApi,
    GoogleWifiFoyerAuthError,
    GoogleWifiFoyerConnectionError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class GoogleWifiFoyerCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll station presence for one Google Wifi network."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: GoogleWifiFoyerApi,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=DEFAULT_SCAN_INTERVAL,
            config_entry=entry,
        )
        self.api = api
        self.entry = entry
        self.access_points: dict[str, dict[str, Any]] = {}
        self._access_points_loaded = False
        self._sensitive_info: dict[str, dict[str, Any]] = {}
        self._sensitive_info_attempted: set[str] = set()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        group_id = self.entry.data["group_id"]

        try:
            stations = await self.api.async_get_stations(group_id)
            if not self._access_points_loaded:
                group = await self.api.async_get_group(group_id)
                if group is None:
                    raise GoogleWifiFoyerConnectionError(
                        "The configured Google Wifi network was not returned"
                    )
                access_points = group.get("accessPoints", [])
                if isinstance(access_points, list):
                    self.access_points = {
                        access_point["id"]: _safe_access_point(access_point)
                        for access_point in access_points
                        if isinstance(access_point, dict)
                        and isinstance(access_point.get("id"), str)
                        and access_point["id"]
                    }
                self._access_points_loaded = True
        except GoogleWifiFoyerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GoogleWifiFoyerConnectionError as err:
            raise UpdateFailed(str(err)) from err

        result: dict[str, dict[str, Any]] = {}
        for station in stations:
            station_id = station.get("id")
            if isinstance(station_id, str) and station_id:
                result[station_id] = station

        missing_ids = sorted(
            set(result) - set(self._sensitive_info) - self._sensitive_info_attempted
        )
        if missing_ids:
            self._sensitive_info_attempted.update(missing_ids)
            try:
                sensitive_info = await self.api.async_get_sensitive_info(
                    group_id, missing_ids
                )
            except (GoogleWifiFoyerAuthError, GoogleWifiFoyerConnectionError) as err:
                _LOGGER.warning(
                    "Could not fetch optional station MAC information: %s", err
                )
            except (UnicodeDecodeError, ValueError) as err:
                _LOGGER.warning(
                    "Could not decode optional station MAC information: %s", err
                )
            except Exception:
                _LOGGER.exception(
                    "Unexpected error fetching optional station MAC information"
                )
            else:
                self._sensitive_info.update(
                    {
                        station_id: info
                        for station_id, info in sensitive_info.items()
                        if station_id in result
                    }
                )

        for station_id, station in result.items():
            sensitive = self._sensitive_info.get(station_id)
            if sensitive:
                result[station_id] = {**station, **sensitive}

        return result


def _safe_access_point(access_point: dict[str, Any]) -> dict[str, Any]:
    """Extract non-secret access point metadata used by Home Assistant."""
    settings = access_point.get("accessPointSettings", {})
    other_settings = (
        settings.get("accessPointOtherSettings", {})
        if isinstance(settings, dict)
        else {}
    )
    room = (
        other_settings.get("roomData", {}) if isinstance(other_settings, dict) else {}
    )
    properties = access_point.get("accessPointProperties", {})
    if not isinstance(properties, dict):
        properties = {}

    return {
        "id": access_point["id"],
        "name": _string_value(other_settings, "apName"),
        "room_name": _string_value(room, "name"),
        "room_type": _string_value(room, "roomType"),
        "ip_address": _string_value(properties, "ipAddress"),
        "last_seen": _string_value(properties, "lastSeenTime"),
        "firmware_version": _string_value(properties, "firmwareVersion"),
        "manufacturer": _string_value(properties, "oemName"),
        "model": _string_value(properties, "hardwareType"),
        "serial_number": _string_value(properties, "serialNumber"),
        "operating_mode": _string_value(properties, "operatingMode"),
        "is_bridged": properties.get("isBridged")
        if isinstance(properties.get("isBridged"), bool)
        else None,
    }


def _string_value(data: Any, key: str) -> str | None:
    """Return a non-empty string from a mapping-like dictionary."""
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value else None
