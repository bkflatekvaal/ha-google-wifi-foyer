"""Binary sensor platform for Google Wifi Foyer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID, DOMAIN
from .coordinator import GoogleWifiFoyerCoordinator, blocking_policy_is_active


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Family Wi-Fi pause sensors."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        new_ids = set(coordinator.family_groups) - known_ids
        if not new_ids:
            return
        async_add_entities(
            GoogleWifiFoyerFamilyPausedSensor(coordinator, entry, family_id)
            for family_id in sorted(new_ids)
        )
        known_ids.update(new_ids)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class GoogleWifiFoyerFamilyPausedSensor(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], BinarySensorEntity
):
    """Show whether internet access is paused for a Family Wi-Fi group."""

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
        return self.coordinator.family_groups.get(self._family_id, {})

    @property
    def name(self) -> str:
        """Return the current Google Home family-group name."""
        return f"{self._family.get('name', 'Family group')} internet paused"

    @property
    def available(self) -> bool:
        """Return whether the family group still exists."""
        return super().available and self._family_id in self.coordinator.family_groups

    @property
    def is_on(self) -> bool:
        """Return whether a blocking policy is active."""
        return blocking_policy_is_active(self._family.get("blocking_policy"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return allowlisted pause and schedule details."""
        policy = self._family.get("blocking_policy")
        attrs: dict[str, Any] = {
            "family_group_id": self._family_id,
            "schedules": self._family.get("schedules", []),
        }
        if isinstance(policy, dict):
            attrs["pause_started_at"] = policy.get("creation_timestamp")
            attrs["pause_expires_at"] = policy.get("expiry_timestamp")
        return {key: value for key, value in attrs.items() if value is not None}

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this entity to the main Wifi network device."""
        return DeviceInfo(identifiers={(DOMAIN, self._group_id)})
