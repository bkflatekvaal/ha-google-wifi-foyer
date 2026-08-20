"""Google Wifi Foyer integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GoogleWifiFoyerApi
from .const import (
    CONF_ANDROID_ID,
    CONF_GROUP_ID,
    CONF_MASTER_TOKEN,
    CONF_NETWORK_NAME,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import GoogleWifiFoyerCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Google Wifi Foyer from a config entry."""
    api = GoogleWifiFoyerApi(
        hass=hass,
        session=async_get_clientsession(hass),
        email=entry.data[CONF_EMAIL],
        master_token=entry.data[CONF_MASTER_TOKEN],
        android_id=entry.data[CONF_ANDROID_ID],
    )

    coordinator = GoogleWifiFoyerCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    device_registry = dr.async_get(hass)
    network_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.data[CONF_GROUP_ID])},
        name=entry.data[CONF_NETWORK_NAME],
        manufacturer="Google",
        model="Google Wifi network",
    )

    for access_point_id, access_point in coordinator.access_points.items():
        device_registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={
                (DOMAIN, f"{entry.data[CONF_GROUP_ID]}_{access_point_id}")
            },
            name=access_point.get("name")
            or access_point.get("room_name")
            or "Google Wifi access point",
            manufacturer=access_point.get("manufacturer") or "Google",
            model=access_point.get("model") or "Google Wifi access point",
            serial_number=access_point.get("serial_number"),
            sw_version=access_point.get("firmware_version"),
            via_device_id=network_device.id,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
