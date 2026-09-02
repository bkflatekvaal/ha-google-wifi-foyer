"""Sensor platform for Google Wifi Foyer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID, DOMAIN
from .coordinator import (
    GoogleWifiFoyerCoordinator,
    access_point_display_name,
    prioritized_station_is_active,
    station_is_guest,
)

# The local API reports whole-second uptime after the request has completed.
# Ignore the resulting small movement in the inferred boot timestamp.
LAST_RESTART_CHANGE_THRESHOLD = timedelta(seconds=30)


@dataclass(frozen=True, kw_only=True)
class GoogleWifiFoyerLocalSensorDescription(SensorEntityDescription):
    """Describe a sensor backed by the access point's local status API."""

    value_fn: Callable[[dict[str, Any]], Any]


def _nested_value(data: dict[str, Any], section: str, key: str) -> Any:
    """Return a value from a local status response."""
    section_data = data.get(section)
    return section_data.get(key) if isinstance(section_data, dict) else None


def _new_version(data: dict[str, Any]) -> str | None:
    value = _nested_value(data, "software", "updateNewVersion")
    if value == "0.0.0.0":
        return "Latest"
    return value if isinstance(value, str) and value else None


def _uptime(data: dict[str, Any]) -> int | float | None:
    value = _nested_value(data, "system", "uptime")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def _last_restart(data: dict[str, Any]) -> datetime | None:
    uptime = _uptime(data)
    return datetime.now(UTC) - timedelta(seconds=uptime) if uptime is not None else None


def _wan_ip(data: dict[str, Any]) -> str | None:
    if _nested_value(data, "wan", "online") is not True:
        return None
    value = _nested_value(data, "wan", "localIpAddress")
    return value if isinstance(value, str) and value else None


def _wan_status(data: dict[str, Any]) -> str | None:
    value = _nested_value(data, "wan", "online")
    if isinstance(value, bool):
        return "Online" if value else "Offline"
    return None


LOCAL_SENSOR_DESCRIPTIONS = (
    GoogleWifiFoyerLocalSensorDescription(
        key="current_version",
        name="Current version",
        icon="mdi:checkbox-marked-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _nested_value(data, "software", "softwareVersion"),
    ),
    GoogleWifiFoyerLocalSensorDescription(
        key="new_version",
        name="New version",
        icon="mdi:update",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_new_version,
    ),
    GoogleWifiFoyerLocalSensorDescription(
        key="uptime",
        name="Uptime",
        icon="mdi:timelapse",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_uptime,
    ),
    GoogleWifiFoyerLocalSensorDescription(
        key="last_restart",
        name="Last restart",
        icon="mdi:restart",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_restart,
    ),
    GoogleWifiFoyerLocalSensorDescription(
        key="local_ip",
        name="WAN IP",
        icon="mdi:access-point-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_wan_ip,
    ),
    GoogleWifiFoyerLocalSensorDescription(
        key="status",
        name="Status",
        icon="mdi:google",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_wan_status,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up access point diagnostic sensors."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    _remove_obsolete_ap_wan_ip_entities(hass, coordinator, entry)

    entities: list[SensorEntity] = [
        GoogleWifiFoyerAccessPointsSensor(coordinator, entry),
        GoogleWifiFoyerSsidSensor(coordinator, entry, guest=False),
        GoogleWifiFoyerSsidSensor(coordinator, entry, guest=True),
        GoogleWifiFoyerTotalConnectedClientsSensor(coordinator, entry),
        GoogleWifiFoyerConnectionTypeClientsSensor(
            coordinator, entry, connection_type="WIRELESS"
        ),
        GoogleWifiFoyerConnectionTypeClientsSensor(
            coordinator, entry, connection_type="WIRED"
        ),
        GoogleWifiFoyerGuestConnectedClientsSensor(hass, coordinator, entry),
        GoogleWifiFoyerPrioritizedDeviceSensor(hass, coordinator, entry),
    ]
    entities.extend(
        GoogleWifiFoyerAccessPointIpSensor(
            coordinator, entry, access_point_id
        )
        for access_point_id in sorted(coordinator.access_points)
    )
    entities.extend(
        GoogleWifiFoyerConnectedClientsSensor(
            hass, coordinator, entry, access_point_id
        )
        for access_point_id in sorted(coordinator.access_points)
    )
    entities.extend(
        GoogleWifiFoyerLocalStatusSensor(
            coordinator, entry, access_point_id, description
        )
        for access_point_id in sorted(coordinator.access_points)
        for description in LOCAL_SENSOR_DESCRIPTIONS
        if description.key != "local_ip"
        or _is_router(coordinator.access_points[access_point_id])
    )
    entities.extend(
        family_entity
        for family_id in sorted(coordinator.family_groups)
        for family_entity in (
            GoogleWifiFoyerFamilyConnectedClientsSensor(
                hass, coordinator, entry, family_id
            ),
            GoogleWifiFoyerFamilyContentFilterSensor(
                coordinator, entry, family_id
            ),
        )
    )
    async_add_entities(entities)

    known_family_ids = set(coordinator.family_groups)

    @callback
    def _add_new_family_entities() -> None:
        new_ids = set(coordinator.family_groups) - known_family_ids
        if not new_ids:
            return
        async_add_entities(
            family_entity
            for family_id in sorted(new_ids)
            for family_entity in (
                GoogleWifiFoyerFamilyConnectedClientsSensor(
                    hass, coordinator, entry, family_id
                ),
                GoogleWifiFoyerFamilyContentFilterSensor(
                    coordinator, entry, family_id
                ),
            )
        )
        known_family_ids.update(new_ids)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_family_entities))


def _is_router(access_point: dict[str, Any]) -> bool:
    """Return whether an access point is the network's primary router."""
    if access_point.get("is_group_root") is True:
        return True
    if access_point.get("is_bridged") is False:
        return True

    operating_mode = access_point.get("operating_mode")
    if isinstance(operating_mode, str) and "nat" in operating_mode.casefold():
        return True

    local_status = access_point.get("local_status")
    return (
        isinstance(local_status, dict)
        and _wan_ip(local_status) is not None
        and _wan_ip(local_status) != access_point.get("ip_address")
    )


def _remove_obsolete_ap_wan_ip_entities(
    hass: HomeAssistant,
    coordinator: GoogleWifiFoyerCoordinator,
    entry: ConfigEntry,
) -> None:
    """Remove Local IP entities that were created for secondary access points."""
    entity_registry = er.async_get(hass)
    group_id = entry.data[CONF_GROUP_ID]

    for access_point_id, access_point in coordinator.access_points.items():
        if _is_router(access_point):
            continue

        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{group_id}_{access_point_id}_local_ip"
        )
        if entity_id is not None:
            entity_registry.async_remove(entity_id)


class GoogleWifiFoyerAccessPointsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """List access points belonging to the configured Wifi group."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:access-point-network"
    _attr_name = "Access points"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.data[CONF_GROUP_ID]}_access_points"

    @property
    def native_value(self) -> int:
        """Return the number of access points in this group."""
        return len(self.coordinator.access_points)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the access points that belong to this group."""
        return {
            "access_points": [
                {
                    "id": access_point_id,
                    "name": access_point_display_name(access_point),
                }
                for access_point_id, access_point in sorted(
                    self.coordinator.access_points.items()
                )
            ]
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to the Wifi group device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_GROUP_ID])}
        )


class GoogleWifiFoyerAccessPointIpSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the IP address and metadata for one access point."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:ip-network"
    _attr_name = "IP address"

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        access_point_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._access_point_id = access_point_id
        self._attr_unique_id = (
            f"{entry.data[CONF_GROUP_ID]}_{access_point_id}_ip_address"
        )

    @property
    def _access_point(self) -> dict[str, Any]:
        return self.coordinator.access_points[self._access_point_id]

    @property
    def native_value(self) -> str | None:
        """Return the access point IP address."""
        return self._access_point.get("ip_address")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return useful access point metadata."""
        access_point = self._access_point
        attrs = {
            "access_point_id": self._access_point_id,
            "room_name": access_point.get("room_name"),
            "room_type": access_point.get("room_type"),
            "last_seen": access_point.get("last_seen"),
            "operating_mode": access_point.get("operating_mode"),
            "is_bridged": access_point.get("is_bridged"),
        }
        return {key: value for key, value in attrs.items() if value is not None}

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to its access point device."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._entry.data[CONF_GROUP_ID]}_{self._access_point_id}",
                )
            }
        )


class GoogleWifiFoyerTotalConnectedClientsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the total number of clients connected to the Wifi network."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:lan-connect"
    _attr_name = "Connected clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._attr_unique_id = f"{self._group_id}_connected_clients"

    @property
    def native_value(self) -> int:
        """Return the number of clients connected across all access points."""
        return sum(
            station.get("connected") is True
            for station in self.coordinator.data.values()
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to the Wifi group device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerConnectionTypeClientsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Count connected clients of one connection type."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        *,
        connection_type: str,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._connection_type = connection_type
        if connection_type == "WIRELESS":
            self._attr_name = "Wi-Fi connected clients"
            self._attr_icon = "mdi:wifi"
            suffix = "wireless_connected_clients"
        else:
            self._attr_name = "Wired connected clients"
            self._attr_icon = "mdi:ethernet"
            suffix = "wired_connected_clients"
        self._attr_unique_id = f"{self._group_id}_{suffix}"

    @property
    def native_value(self) -> int:
        """Return the number of connected clients of this type."""
        return sum(
            station.get("connected") is True
            and station.get("connectionType") == self._connection_type
            for station in self.coordinator.data.values()
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to the Wifi group device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerSsidSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the SSID of the main or guest wireless network."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:wifi"

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        *,
        guest: bool,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._guest = guest
        kind = "guest" if guest else "main"
        self._attr_name = f"{kind.capitalize()} SSID"
        self._attr_unique_id = f"{self._group_id}_{kind}_ssid"

    @property
    def _network(self) -> dict[str, Any] | None:
        return (
            self.coordinator.guest_network
            if self._guest
            else self.coordinator.main_network
        )

    @property
    def available(self) -> bool:
        """Return whether Google supplied this wireless network's SSID."""
        return super().available and isinstance(self._network, dict)

    @property
    def native_value(self) -> str | None:
        """Return the SSID."""
        network = self._network
        return network.get("ssid") if isinstance(network, dict) else None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this entity to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerGuestConnectedClientsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show clients currently connected to the guest network."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-multiple-outline"
    _attr_name = "Guest connected clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entity_registry = er.async_get(hass)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._attr_unique_id = f"{self._group_id}_guest_connected_clients"

    @property
    def _connected_stations(self) -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            (
                (station_id, station)
                for station_id, station in self.coordinator.data.items()
                if station.get("connected") is True and station_is_guest(station)
            ),
            key=lambda item: _station_name(item[1]).casefold(),
        )

    @property
    def native_value(self) -> int:
        """Return the number of connected guest clients."""
        return len(self._connected_stations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the connected guest clients."""
        return {
            "clients": [
                {
                    "station_id": station_id,
                    "name": _station_name(station),
                    "entity_id": self._entity_registry.async_get_entity_id(
                        "device_tracker", DOMAIN, f"{self._group_id}_{station_id}"
                    ),
                    "ip_address": _station_ip(station),
                }
                for station_id, station in self._connected_stations
            ]
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this entity to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerPrioritizedDeviceSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the one device currently prioritized on the Wifi network."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:priority-high"
    _attr_name = "Prioritized device"

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._entity_registry = er.async_get(hass)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._attr_unique_id = f"{self._group_id}_prioritized_device"

    @property
    def _active_priority(self) -> dict[str, Any] | None:
        priority = self.coordinator.prioritized_station
        return priority if prioritized_station_is_active(priority) else None

    @property
    def native_value(self) -> str:
        """Return the prioritized station's current friendly name."""
        priority = self._active_priority
        if priority is None:
            return "None"
        station = self.coordinator.data.get(priority["station_id"], {})
        return _station_name(station)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the prioritized tracker and expiry time."""
        priority = self._active_priority
        if priority is None:
            return {}
        station_id = priority["station_id"]
        unique_id = f"{self._group_id}_{station_id}"
        return {
            "station_id": station_id,
            "entity_id": self._entity_registry.async_get_entity_id(
                "device_tracker", DOMAIN, unique_id
            ),
            "prioritization_ends_at": priority.get("ends_at"),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerLocalStatusSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Expose one value from an access point's local status endpoint."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        access_point_id: str,
        description: GoogleWifiFoyerLocalSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._access_point_id = access_point_id
        self.entity_description = description
        self._stable_last_restart: datetime | None = None
        self._attr_unique_id = (
            f"{entry.data[CONF_GROUP_ID]}_{access_point_id}_{description.key}"
        )

    @property
    def _local_status(self) -> dict[str, Any] | None:
        value = self.coordinator.access_points[self._access_point_id].get(
            "local_status"
        )
        return value if isinstance(value, dict) else None

    @property
    def available(self) -> bool:
        """Return whether the local endpoint supplied status data."""
        return super().available and self._local_status is not None

    @property
    def native_value(self) -> Any:
        """Return the selected local status value."""
        if (status := self._local_status) is None:
            return None
        value = self.entity_description.value_fn(status)
        if self.entity_description.key != "last_restart" or not isinstance(
            value, datetime
        ):
            return value

        if (
            self._stable_last_restart is None
            or abs(value - self._stable_last_restart)
            >= LAST_RESTART_CHANGE_THRESHOLD
        ):
            self._stable_last_restart = value
        return self._stable_last_restart

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to its access point device."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._entry.data[CONF_GROUP_ID]}_{self._access_point_id}",
                )
            }
        )


class GoogleWifiFoyerConnectedClientsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the clients currently connected to one access point."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:lan-connect"
    _attr_name = "Connected clients"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        access_point_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entity_registry = er.async_get(hass)
        self._entry = entry
        self._group_id = entry.data[CONF_GROUP_ID]
        self._access_point_id = access_point_id
        self._attr_unique_id = (
            f"{self._group_id}_{access_point_id}_connected_clients"
        )

    @property
    def _connected_stations(self) -> list[tuple[str, dict[str, Any]]]:
        return sorted(
            (
                (station_id, station)
                for station_id, station in self.coordinator.data.items()
                if station.get("connected") is True
                and station.get("apId") == self._access_point_id
            ),
            key=lambda item: _station_name(item[1]).casefold(),
        )

    @property
    def native_value(self) -> int:
        """Return the number of connected clients."""
        return len(self._connected_stations)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return structured client data suitable for a topology card."""
        clients = []
        for station_id, station in self._connected_stations:
            unique_id = f"{self._group_id}_{station_id}"
            client = {
                "station_id": station_id,
                "name": _station_name(station),
                "entity_id": self._entity_registry.async_get_entity_id(
                    "device_tracker", DOMAIN, unique_id
                ),
                "ip_address": _station_ip(station),
                "mac_address": station.get("macAddress") or station.get("mac"),
                "connection_type": station.get("connectionType"),
                "wireless_band": station.get("wirelessBand"),
            }
            clients.append(
                {key: value for key, value in client.items() if value is not None}
            )
        return {"access_point_id": self._access_point_id, "clients": clients}

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this sensor to its access point device."""
        return DeviceInfo(
            identifiers={
                (
                    DOMAIN,
                    f"{self._group_id}_{self._access_point_id}",
                )
            }
        )


class GoogleWifiFoyerFamilyConnectedClientsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Count connected clients in one Family Wi-Fi group."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-group"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        family_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._entity_registry = er.async_get(hass)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._family_id = family_id
        self._attr_unique_id = (
            f"{self._group_id}_family_{family_id}_connected_clients"
        )

    @property
    def _family(self) -> dict[str, Any]:
        return self.coordinator.family_groups.get(self._family_id, {})

    @property
    def name(self) -> str:
        """Return the current Google Home family-group name."""
        return f"{self._family.get('name', 'Family group')} connected clients"

    @property
    def available(self) -> bool:
        """Return whether the family group still exists."""
        return super().available and self._family_id in self.coordinator.family_groups

    @property
    def native_value(self) -> int:
        """Return the number of connected members."""
        return sum(
            self.coordinator.data.get(station_id, {}).get("connected") is True
            for station_id in self._family.get("member_ids", [])
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return family membership using safe names and entity IDs."""
        members = []
        for station_id in self._family.get("member_ids", []):
            station = self.coordinator.data.get(station_id, {})
            unique_id = f"{self._group_id}_{station_id}"
            members.append(
                {
                    "name": _station_name(station),
                    "entity_id": self._entity_registry.async_get_entity_id(
                        "device_tracker", DOMAIN, unique_id
                    ),
                    "connected": station.get("connected") is True,
                }
            )
        return {
            "family_group_id": self._family_id,
            "member_count": len(self._family.get("member_ids", [])),
            "members": members,
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this entity to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerFamilyContentFilterSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """Show the content-filtering mode for one Family Wi-Fi group."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:shield-check"

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        family_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._family_id = family_id
        self._attr_unique_id = f"{self._group_id}_family_{family_id}_content_filter"

    @property
    def _family(self) -> dict[str, Any]:
        return self.coordinator.family_groups.get(self._family_id, {})

    @property
    def name(self) -> str:
        """Return the current Google Home family-group name."""
        return f"{self._family.get('name', 'Family group')} content filter"

    @property
    def available(self) -> bool:
        """Return whether the family group still exists."""
        return super().available and self._family_id in self.coordinator.family_groups

    @property
    def native_value(self) -> str:
        """Return whether content filtering is enabled."""
        return (
            "On"
            if self._family.get("content_filter") == "CLOUD_FILTERING_ENABLED"
            else "Off"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return Google's raw filtering mode when a policy is present."""
        mode = self._family.get("content_filter")
        return {"filtering_mode": mode} if mode is not None else None

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this entity to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


def _station_name(station: dict[str, Any]) -> str:
    """Return the best available client name."""
    for key in ("friendlyName", "automaticFriendlyName", "dhcpHostname"):
        value = station.get(key)
        if isinstance(value, str) and value and value != "Unnamed device":
            return value
    return "Unnamed device"


def _station_ip(station: dict[str, Any]) -> str | None:
    """Return the first available client IP address."""
    value = station.get("ipAddress")
    if isinstance(value, str) and value:
        return value
    values = station.get("ipAddresses")
    if isinstance(values, list):
        return next(
            (value for value in values if isinstance(value, str) and value),
            None,
        )
    return None
