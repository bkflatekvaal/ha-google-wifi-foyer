"""Data coordinator for Google Wifi Foyer."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
        self.family_groups: dict[str, dict[str, Any]] = {}
        self.station_policies: dict[str, dict[str, Any]] = {}
        self.prioritized_station: dict[str, Any] | None = None
        self.main_network: dict[str, Any] | None = None
        self.guest_network: dict[str, Any] | None = None
        self.dhcp_reservations: dict[str, str] = {}
        self._sensitive_info: dict[str, dict[str, Any]] = {}
        self._sensitive_info_attempted: set[str] = set()

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        group_id = self.entry.data["group_id"]

        try:
            stations = await self.api.async_get_stations(group_id)
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
            self.family_groups, self.station_policies = _safe_family_wifi(group)
            self.prioritized_station = _safe_prioritized_station(group)
            self.main_network, self.guest_network = _safe_wireless_networks(group)
            self.dhcp_reservations = _safe_dhcp_reservations(group)

            local_status_results = await asyncio.gather(
                *(
                    self.api.async_get_local_status(access_point["ip_address"])
                    for access_point in self.access_points.values()
                    if access_point.get("ip_address")
                ),
                return_exceptions=True,
            )
            access_points_with_ip = [
                access_point
                for access_point in self.access_points.values()
                if access_point.get("ip_address")
            ]
            for access_point, local_status in zip(
                access_points_with_ip, local_status_results, strict=True
            ):
                if isinstance(local_status, Exception):
                    access_point["local_status"] = None
                    _LOGGER.debug(
                        "Could not update local status for access point %s: %s",
                        access_point["id"],
                        local_status,
                    )
                else:
                    access_point["local_status"] = local_status
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

    lighting: dict[str, Any] | None = None
    for container in (settings, other_settings, properties, access_point):
        if not isinstance(container, dict):
            continue
        for key in (
            "lighting",
            "lightingSettings",
            "accessPointLightingSettings",
            "lightSettings",
        ):
            candidate = container.get(key)
            if isinstance(candidate, dict):
                lighting = candidate
                break
        if lighting is not None:
            break
    # Foyer omits the zero-valued scalar when the indicator is off.
    intensity = lighting.get("intensity", 0) if lighting is not None else None

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
        "is_group_root": properties.get("isGroupRoot")
        if isinstance(properties.get("isGroupRoot"), bool)
        else None,
        "is_bridged": properties.get("isBridged")
        if isinstance(properties.get("isBridged"), bool)
        else None,
        "indicator_intensity": intensity
        if isinstance(intensity, (int, float)) and not isinstance(intensity, bool)
        else None,
    }


def _safe_family_wifi(
    group: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract an allowlisted view of Family Wi-Fi settings."""
    settings = group.get("groupSettings")
    if not isinstance(settings, dict):
        return {}, {}
    station_sets = settings.get("stationSets")
    family_settings = settings.get("familyHubSettings")
    if not isinstance(station_sets, list) or not isinstance(family_settings, dict):
        return {}, {}

    family_groups: dict[str, dict[str, Any]] = {}
    for station_set in station_sets:
        if not isinstance(station_set, dict):
            continue
        station_set_id = _string_value(station_set, "id")
        if station_set_id is None:
            continue
        members = station_set.get("members")
        member_ids = (
            [
                station_id
                for member in members
                if isinstance(member, dict)
                if (station_id := _string_value(member, "stationId")) is not None
            ]
            if isinstance(members, list)
            else []
        )
        family_groups[station_set_id] = {
            "id": station_set_id,
            "name": _string_value(station_set, "name") or "Family group",
            "member_ids": member_ids,
            "blocking_policy": None,
            "content_filter": None,
            "schedules": [],
        }

    for policy in _station_set_blocking_policies(family_settings):
        station_set_ids = _string_items(policy, "stationSetIds")
        if (station_set_id := _string_value(policy, "stationSetId")) is not None:
            station_set_ids.append(station_set_id)
        blocking_policy = _safe_blocking_policy(
            policy.get("blockingPolicy", policy)
        )
        for station_set_id in station_set_ids:
            if station_set_id in family_groups:
                family_groups[station_set_id]["blocking_policy"] = blocking_policy

    for policy in _dict_items(family_settings, "contentFilteringPolicies"):
        mode = _string_value(policy, "safeFilteringMode")
        for station_set_id in _string_items(policy, "stationSetIds"):
            if station_set_id in family_groups:
                family_groups[station_set_id]["content_filter"] = mode

    for schedule in _dict_items(family_settings, "blockingSchedules"):
        safe_schedule = _safe_schedule(schedule)
        for station_set_id in _string_items(schedule, "stationSetIds"):
            if station_set_id in family_groups:
                family_groups[station_set_id]["schedules"].append(safe_schedule)

    station_policies = {
        station_id: blocking_policy
        for policy in _dict_items(family_settings, "stationPolicies")
        if (station_id := _string_value(policy, "stationId")) is not None
        if (blocking_policy := _safe_blocking_policy(policy.get("blockingPolicy")))
        is not None
    }
    return family_groups, station_policies


def _safe_prioritized_station(group: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the active prioritized-station fields."""
    settings = group.get("groupSettings")
    lan_settings = settings.get("lanSettings") if isinstance(settings, dict) else None
    priority = (
        lan_settings.get("prioritizedStation")
        if isinstance(lan_settings, dict)
        else None
    )
    if not isinstance(priority, dict):
        return None
    station_id = _string_value(priority, "stationId")
    if station_id is None:
        return None
    return {
        "station_id": station_id,
        "ends_at": _string_value(priority, "prioritizationEndTime"),
    }


def _safe_wireless_networks(
    group: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Extract the non-secret main and guest Wi-Fi settings."""
    settings = group.get("groupSettings")
    if not isinstance(settings, dict):
        return None, None

    wlan = settings.get("wlanSettings")
    wireless = settings.get("wirelessSettings")
    guest = settings.get("guestWirelessSettings")
    main_ssid = _string_value(wireless, "ssid") or _string_value(
        wlan, "privateSsid"
    )
    main = {"ssid": main_ssid} if main_ssid is not None else None

    if isinstance(guest, dict):
        guest_ssid = _string_value(guest, "ssid")
        enabled_value = guest.get("enabled")
        guest_enabled = (
            enabled_value if isinstance(enabled_value, bool) else bool(guest_ssid)
        )
        return main, {"enabled": guest_enabled, "ssid": guest_ssid}

    if not isinstance(wlan, dict):
        return main, None
    guest_ssid = _string_value(wlan, "guestSsid")
    guest_enabled = next(
        (
            wlan[key]
            for key in ("guestNetworkEnabled", "guestEnabled")
            if isinstance(wlan.get(key), bool)
        ),
        bool(guest_ssid),
    )
    return main, {"enabled": guest_enabled, "ssid": guest_ssid}


def _safe_dhcp_reservations(group: dict[str, Any]) -> dict[str, str]:
    """Return DHCP reservations keyed by station ID."""
    settings = group.get("groupSettings")
    if not isinstance(settings, dict):
        return {}

    reservations: dict[str, str] = {}
    containers = [
        value
        for key in ("lanSettings", "dhcpSettings")
        if isinstance((value := settings.get(key)), dict)
    ]
    reservation_items = [
        reservation
        for container in containers
        for key in ("dhcpReservations", "staticIpMappings")
        for reservation in _dict_items(container, key)
    ]
    for reservation in reservation_items:
        station_id = _string_value(reservation, "stationId")
        ip_address = _string_value(reservation, "ipAddress") or _string_value(
            reservation, "reservedIpAddress"
        )
        if station_id is not None and ip_address is not None:
            reservations[station_id] = ip_address
    return reservations


def station_is_guest(station: dict[str, Any]) -> bool:
    """Return whether Foyer identifies a station as a guest client."""
    for key in (
        "connectedToGuestNetwork",
        "isGuest",
        "isGuestNetwork",
        "guest",
        "guestNetwork",
        "onGuestNetwork",
    ):
        value = station.get(key)
        if isinstance(value, bool):
            return value

    for key in ("networkType", "wirelessNetworkType", "network"):
        value = station.get(key)
        if isinstance(value, str):
            return value.casefold() in {"guest", "guest_network", "guestnetwork"}
    return False


def _dict_items(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_items(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _safe_blocking_policy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("blocked") is False:
        return None
    return {
        "creation_timestamp": _string_value(value, "creationTimestamp"),
        "expiry_timestamp": _string_value(value, "expiryTimestamp"),
    }


def _station_set_blocking_policies(value: Any) -> list[dict[str, Any]]:
    """Find station-set blocking policies across Foyer response variants."""
    policies: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            policies.extend(_station_set_blocking_policies(item))
        return policies
    if not isinstance(value, dict):
        return policies

    has_station_set = isinstance(value.get("stationSetId"), str) or isinstance(
        value.get("stationSetIds"), list
    )
    has_blocking_data = isinstance(value.get("blockingPolicy"), dict) or any(
        key in value for key in ("blocked", "creationTimestamp", "expiryTimestamp")
    )
    if has_station_set and has_blocking_data:
        policies.append(value)
        return policies

    for nested in value.values():
        if isinstance(nested, (dict, list)):
            policies.extend(_station_set_blocking_policies(nested))
    return policies


def _safe_schedule(value: dict[str, Any]) -> dict[str, Any]:
    schedule = value.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    durations = []
    for duration in _dict_items(schedule, "scheduleDurations"):
        start = duration.get("startTime")
        end = duration.get("endTime")
        durations.append({
            "start_day": _string_value(duration, "startDay"),
            "start_time": _safe_clock(start),
            "end_day": _string_value(duration, "endDay"),
            "end_time": _safe_clock(end),
        })
    return {
        "id": _string_value(value, "id"),
        "name": _string_value(schedule, "name"),
        "time_zone": _string_value(schedule, "timeZoneId"),
        "durations": durations,
    }


def _safe_clock(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    hours = value.get("hours")
    minutes = value.get("minutes", 0)
    if not isinstance(hours, int) or not isinstance(minutes, int):
        return None
    return f"{hours:02d}:{minutes:02d}"


def _string_value(data: Any, key: str) -> str | None:
    """Return a non-empty string from a mapping-like dictionary."""
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def access_point_display_name(access_point: dict[str, Any]) -> str:
    """Return an access point name that includes its room when available."""
    name = access_point.get("name")
    room_name = access_point.get("room_name")

    if isinstance(name, str) and name:
        if (
            isinstance(room_name, str)
            and room_name
            and room_name.casefold() != name.casefold()
        ):
            return f"{name} ({room_name})"
        return name
    if isinstance(room_name, str) and room_name:
        return room_name
    return "Google Wifi access point"


def blocking_policy_is_active(policy: Any) -> bool:
    """Return whether a Family Wi-Fi blocking policy is currently active."""
    if not isinstance(policy, dict):
        return False
    created = policy.get("creation_timestamp")
    expiry = policy.get("expiry_timestamp")
    if not isinstance(expiry, str) or not expiry:
        return True
    # Foyer represents an indefinite pause with the Unix epoch. Resuming the
    # group leaves a tombstone whose expiry is identical to its creation time.
    if isinstance(created, str) and created and expiry == created:
        return False
    try:
        expiry_time = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry_time.tzinfo is None:
        expiry_time = expiry_time.replace(tzinfo=UTC)
    if expiry_time == datetime(1970, 1, 1, tzinfo=UTC):
        return True
    return expiry_time > datetime.now(UTC)


def prioritized_station_is_active(priority: Any) -> bool:
    """Return whether a prioritized-station selection is still active."""
    if not isinstance(priority, dict) or not priority.get("station_id"):
        return False
    ends_at = priority.get("ends_at")
    if not isinstance(ends_at, str) or not ends_at:
        return True
    try:
        end_time = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=UTC)
    return end_time > datetime.now(UTC)
