"""Access-point indicator controls for Google Wifi Foyer."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GROUP_ID, DOMAIN
from .coordinator import GoogleWifiFoyerCoordinator

OPTIONS = ("Off", "Low", "High")
INTENSITIES = {"Off": 0, "Low": 50, "High": 100}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up access-point indicator selectors."""
    coordinator: GoogleWifiFoyerCoordinator = entry.runtime_data
    async_add_entities(
        GoogleWifiFoyerIndicatorSelect(coordinator, entry, access_point_id)
        for access_point_id in sorted(coordinator.access_points)
    )


class GoogleWifiFoyerIndicatorSelect(
    CoordinatorEntity[GoogleWifiFoyerCoordinator], SelectEntity
):
    """Select an access point's status-light brightness."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:led-on"
    _attr_name = "Indicator brightness"
    _attr_options = list(OPTIONS)

    def __init__(
        self,
        coordinator: GoogleWifiFoyerCoordinator,
        entry: ConfigEntry,
        access_point_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._group_id = entry.data[CONF_GROUP_ID]
        self._access_point_id = access_point_id
        self._attr_unique_id = (
            f"{self._group_id}_{access_point_id}_indicator_brightness"
        )

    @property
    def current_option(self) -> str | None:
        """Return the current indicator setting."""
        intensity = self.coordinator.access_points.get(
            self._access_point_id, {}
        ).get("indicator_intensity")
        if not isinstance(intensity, (int, float)) or isinstance(intensity, bool):
            return None
        if intensity <= 0:
            return "Off"
        return "Low" if intensity <= 50 else "High"

    async def async_select_option(self, option: str) -> None:
        """Set the indicator brightness."""
        await self.coordinator.api.async_set_ap_indicator(
            self._access_point_id, INTENSITIES[option]
        )
        await self.coordinator.async_request_refresh()

    @property
    def device_info(self) -> DeviceInfo:
        """Attach this selector to its access point device."""
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"{self._group_id}_{self._access_point_id}")
            }
        )
