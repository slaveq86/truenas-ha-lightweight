"""Polling coordinator for TrueNAS."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SystemInfo,
    TrueNASAuthError,
    TrueNASClient,
    TrueNASData,
    TrueNASError,
    TrueNASPermissionError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER

BOOT_TIME_TOLERANCE = timedelta(minutes=2)

type TrueNASConfigEntry = ConfigEntry[TrueNASCoordinator]


class TrueNASCoordinator(DataUpdateCoordinator[TrueNASData]):
    """Fetches all TrueNAS data in one parallel round-trip per interval."""

    config_entry: TrueNASConfigEntry

    def __init__(self, hass: HomeAssistant, entry: TrueNASConfigEntry, client: TrueNASClient) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.client = client
        self._denied: set[str] = set()

    async def _async_update_data(self) -> TrueNASData:
        try:
            system, alerts, pools, apps = await asyncio.gather(
                self.client.system_info(),
                self._optional("alert.list", self.client.alerts, []),
                self._optional("pool.query", self.client.pools, {}),
                self._optional("app.query", self.client.apps, {}),
            )
        except TrueNASAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TrueNASError as err:
            raise UpdateFailed(str(err)) from err

        self._stabilize_boot_time(system)
        return TrueNASData(
            system=system,
            stats=self.client.realtime_stats(),
            alerts=alerts,
            pools=pools,
            apps=apps,
        )

    def _stabilize_boot_time(self, system: SystemInfo) -> None:
        """Keep the previous boot time unless it moved more than poll jitter (i.e. a reboot)."""
        previous = self.data.system.boot_time if self.data else None
        if (
            previous is not None
            and system.boot_time is not None
            and abs(system.boot_time - previous) < BOOT_TIME_TOLERANCE
        ):
            system.boot_time = previous

    async def _optional[T](self, method: str, fetch: Callable[[], Awaitable[T]], default: T) -> T:
        """Fetch data the key's role may not cover without failing the whole update."""
        try:
            return await fetch()
        except TrueNASPermissionError:
            if method not in self._denied:
                self._denied.add(method)
                LOGGER.warning("API key is not permitted to call %s; related entities are skipped", method)
            return default
