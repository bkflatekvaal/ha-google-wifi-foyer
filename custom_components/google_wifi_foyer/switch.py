"""Writable network controls for Google Wifi Foyer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID, CONF_GUEST_PSK, CONF_GUEST_SSID, DOMAIN
from .coordinator import GoogleWifiFoyerCoordinator, blocking_policy_is_active


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up guest-network and Family Wi-Fi switches."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    known_ids: set[str] = set()
    async_add_entities([GoogleWifiFoyerGuestNetworkSwitch(coordinator, entry)])

    @callback
    def _add_new_entities() -> None:
        new_ids = set(coordinator.family_groups) - known_ids
        if new_ids:
            async_add_entities(
                GoogleWifiFoyerFamilyPausedSwitch(coordinator, entry, family_id)
                for family_id in sorted(new_ids)
            )
            known_ids.update(new_ids)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class GoogleWifiFoyerGuestNetworkSwitch(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SwitchEntity
):
    """Enable or disable guest Wi-Fi."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:wifi-star"
    _attr_name = "Guest network"

    def __init__(
        self, coordinator: GoogleWifiFoyerCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._group_id = entry.data[CONF_GROUP_ID]
        self._attr_unique_id = f"{self._group_id}_guest_network"

    @property
    def available(self) -> bool:
        """Return whether guest-network settings are available."""
        return super().available and self.coordinator.guest_network is not None

    @property
    def is_on(self) -> bool:
        """Return whether guest Wi-Fi is enabled."""
        guest = self.coordinator.guest_network
        return isinstance(guest, dict) and guest.get("enabled") is True

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the guest SSID."""
        guest = self.coordinator.guest_network
        return (
            {"ssid": guest["ssid"]}
            if isinstance(guest, dict) and guest.get("ssid")
            else {}
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable guest Wi-Fi."""
        guest = self.coordinator.guest_network
        ssid = self._entry.options.get(CONF_GUEST_SSID) or (
            guest.get("ssid") if isinstance(guest, dict) else None
        )
        if not isinstance(ssid, str) or not ssid:
            raise HomeAssistantError("Guest network has no configured SSID")
        await self.coordinator.api.async_set_guest_network_enabled(
            self._group_id,
            True,
            ssid,
            self._entry.options.get(CONF_GUEST_PSK),
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable guest Wi-Fi."""
        guest = self.coordinator.guest_network
        ssid = self._entry.options.get(CONF_GUEST_SSID) or (
            guest.get("ssid") if isinstance(guest, dict) else None
        )
        if not isinstance(ssid, str) or not ssid:
            raise HomeAssistantError("Guest network has no configured SSID")
        await self.coordinator.api.async_set_guest_network_enabled(
            self._group_id,
            False,
            ssid,
            self._entry.options.get(CONF_GUEST_PSK),
        )
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this switch to the network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})


class GoogleWifiFoyerFamilyPausedSwitch(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SwitchEntity
):
    """Pause or resume a Family Wi-Fi group."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:pause-network"

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        family_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._family_id = family_id
        self._attr_unique_id = f"{self._group_id}_family_{family_id}_paused"

    @property
    def _family(self) -> dict[str, Any]:
        """Return the current family-group data."""
        return self.coordinator.family_groups.get(self._family_id, {})

    @property
    def name(self) -> str:
        """Return the current family-group name."""
        return f"{self._family.get('name', 'Family group')} internet paused"

    @property
    def available(self) -> bool:
        """Return whether the family group still exists."""
        return super().available and self._family_id in self.coordinator.family_groups

    @property
    def is_on(self) -> bool:
        """Return whether internet access is paused."""
        return blocking_policy_is_active(self._family.get("blocking_policy"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Pause internet access for the group."""
        await self.coordinator.api.async_set_family_paused(
            self._group_id, self._family_id, True
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Resume internet access for the group."""
        await self.coordinator.api.async_set_family_paused(
            self._group_id, self._family_id, False
        )
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return family and schedule details."""
        return {
            "family_group_id": self._family_id,
            "schedules": self._family.get("schedules", []),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this switch to the network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})
