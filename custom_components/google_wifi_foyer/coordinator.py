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


class GoogleWifiFoyerCoordinator(
    DataUpdateCoordinator[dict[str, dict[str, Any]]]
):
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

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        group_id = self.entry.data["group_id"]

        try:
            stations = await self.api.async_get_stations(group_id)
        except GoogleWifiFoyerAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GoogleWifiFoyerConnectionError as err:
            raise UpdateFailed(str(err)) from err

        result: dict[str, dict[str, Any]] = {}
        for station in stations:
            station_id = station.get("id")
            if isinstance(station_id, str) and station_id:
                result[station_id] = station

        return result
