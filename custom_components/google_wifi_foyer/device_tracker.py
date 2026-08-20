"""Device tracker platform for Google Wifi Foyer."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID
from .coordinator import GoogleWifiFoyerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up station trackers."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_ids = set(coordinator.data) - known_ids
        if not new_ids:
            return

        entities = [
            GoogleWifiFoyerStationTracker(coordinator, entry, station_id)
            for station_id in sorted(new_ids)
        ]
        known_ids.update(new_ids)
        async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class GoogleWifiFoyerStationTracker(
    CoordinatorEntity[GoogleWifiFoyerCoordinator],
    ScannerEntity,
):
    """Presence tracker for one Foyer station."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        station_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._station_id = station_id
        self._attr_unique_id = f"{entry.data[CONF_GROUP_ID]}_{station_id}"
        self._attr_name = self._station_name

    @property
    def _station(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._station_id, {})

    @property
    def _station_name(self) -> str:
        station = self._station
        for key in ("friendlyName", "automaticFriendlyName", "dhcpHostname"):
            value = station.get(key)
            if isinstance(value, str) and value and value != "Unnamed device":
                return value
        return "Unnamed device"

    @property
    def name(self) -> str:
        """Return current friendly name."""
        return self._station_name

    @property
    def is_connected(self) -> bool:
        """Return whether the station is connected."""
        return self._station.get("connected") is True

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Keep all stations enabled until the user disables unwanted ones."""
        return True

    @property
    def ip_address(self) -> str | None:
        """Return current IP address reported by Google."""
        value = self._station.get("ipAddress")
        if isinstance(value, str) and value:
            return value

        values = self._station.get("ipAddresses")
        if isinstance(values, list):
            for item in values:
                if isinstance(item, str) and item:
                    return item
        return None

    @property
    def hostname(self) -> str | None:
        """Return DHCP hostname."""
        value = self._station.get("dhcpHostname")
        return value if isinstance(value, str) and value else None

    @property
    def mac_address(self) -> str | None:
        """Return the normalized MAC address reported by sensitive info."""
        return _station_mac(self._station)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return useful Foyer metadata."""
        station = self._station
        status = station.get("status")
        last_seen = station.get("lastSeen")
        access_point = self.coordinator.access_points.get(station.get("apId"), {})

        attrs: dict[str, Any] = {
            "google_wifi_name": self._station_name,
            "connection_type": _human_connection_type(station.get("connectionType")),
            "wireless_band": _human_wireless_band(station.get("wirelessBand")),
            "access_point_id": station.get("apId"),
            "friendly_type": station.get("friendlyType"),
            "manufacturer": station.get("curatedOuiName"),
            "station_type": station.get("stationType"),
            "wireless_capability": station.get("wirelessCap"),
            "rx_spatial_streams": station.get("numberOfRxSpatialStream"),
            "ipv6_addresses": station.get("ipv6Addresses"),
            "access_point_name": access_point.get("name")
            or access_point.get("room_name"),
            "access_point_ip_address": access_point.get("ip_address"),
            "access_point_room": access_point.get("room_name"),
        }

        if isinstance(status, dict):
            attrs["foyer_status"] = status.get("type")

        if isinstance(last_seen, str):
            attrs["last_seen"] = last_seen

        return {key: value for key, value in attrs.items() if value is not None}


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _station_mac(station: dict[str, Any]) -> str | None:
    """Return a normalized MAC address when Google provides one."""
    for key in ("macAddress", "mac"):
        value = station.get(key)
        if not isinstance(value, str):
            continue

        normalized = format_mac(value.strip())
        if re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", normalized):
            return normalized

    return None


def _human_connection_type(value: Any) -> str | None:
    if value == "WIRELESS":
        return "Wireless"
    if value == "WIRED":
        return "Wired"
    return _string_or_none(value)


def _human_wireless_band(value: Any) -> str | None:
    mapping = {
        "BAND_2400_MHZ": "2.4 GHz",
        "BAND_5000_MHZ": "5 GHz",
        "BAND_6000_MHZ": "6 GHz",
        "NOT_APPLICABLE": None,
    }
    if value in mapping:
        return mapping[value]
    return _string_or_none(value)
