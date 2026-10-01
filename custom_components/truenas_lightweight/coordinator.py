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
    Alert,
    SystemInfo,
    TrueNASAuthError,
    TrueNASClient,
    TrueNASData,
    TrueNASError,
    TrueNASPermissionError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, EVENT_ALERT, LOGGER

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
            system, alerts, pools, apps, rsync_tasks, snapshot_tasks = await asyncio.gather(
                self.client.system_info(),
                # None (not []) when denied, so a denial isn't mistaken for every alert clearing.
                self._optional("alert.list", self.client.alerts, None),
                self._optional("pool.query", self.client.pools, {}),
                self._optional("app.query", self.client.apps, {}),
                self._optional("rsynctask.query", self.client.rsync_tasks, {}),
                self._optional("pool.snapshottask.query", self.client.snapshot_tasks, {}),
            )
        except TrueNASAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TrueNASError as err:
            raise UpdateFailed(str(err)) from err

        self._stabilize_boot_time(system)
        if alerts is not None and self.data is not None:
            self._fire_alert_events(self.data.active_alerts, [a for a in alerts if not a.dismissed], system)
        return TrueNASData(
            system=system,
            stats=self.client.realtime_stats(),
            alerts=alerts or [],
            pools=pools,
            apps=apps,
            rsync_tasks=rsync_tasks,
            snapshot_tasks=snapshot_tasks,
        )

    def _fire_alert_events(self, previous: list[Alert], current: list[Alert], system: SystemInfo) -> None:
        """Fire EVENT_ALERT for alerts that appeared or cleared since the last poll."""
        before = {a.uuid: a for a in previous}
        after = {a.uuid: a for a in current}
        changes = [("raised", after[u]) for u in after.keys() - before.keys()]
        changes += [("cleared", before[u]) for u in before.keys() - after.keys()]
        for action, alert in sorted(changes, key=lambda c: (-c[1].severity, c[1].uuid)):
            self.hass.bus.async_fire(
                EVENT_ALERT,
                {
                    "action": action,
                    "config_entry_id": self.config_entry.entry_id,
                    "hostname": system.hostname,
                    "uuid": alert.uuid,
                    "level": alert.level,
                    "klass": alert.klass,
                    "message": alert.message,
                    "datetime": alert.datetime.isoformat() if alert.datetime else None,
                },
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
