"""Sensor platform for Google Wifi Foyer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID, DOMAIN
from .coordinator import GoogleWifiFoyerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up access point diagnostic sensors."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        GoogleWifiFoyerAccessPointsSensor(coordinator, entry)
    ]
    entities.extend(
        GoogleWifiFoyerAccessPointIpSensor(
            coordinator, entry, access_point_id
        )
        for access_point_id in sorted(coordinator.access_points)
    )
    async_add_entities(entities)


class GoogleWifiFoyerAccessPointsSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SensorEntity
):
    """List access points belonging to the configured Wifi group."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True
    _attr_icon = "mdi:access-point-network"
    _attr_name = "Access points"

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
                    "name": access_point.get("name")
                    or access_point.get("room_name")
                    or "Google Wifi access point",
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
