"""Polling coordinator for TrueNAS."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    Alert,
    Disk,
    SystemInfo,
    TrueNASAuthError,
    TrueNASClient,
    TrueNASData,
    TrueNASError,
    TrueNASPermissionError,
)
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DISK_INTERVAL, DOMAIN, EVENT_ALERT, LOGGER

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
        # Active alerts by uuid from the last successful alert.list; None until known (startup, denied).
        self._alert_baseline: dict[str, Alert] | None = None
        self._disks: dict[str, Disk] = {}
        # time.monotonic() of the last disk fetch attempt; wall-clock jumps must not stall the refresh.
        self._disks_at: float | None = None
        self._disk_errors: set[str] = set()

    async def _async_update_data(self) -> TrueNASData:
        try:
            system, alerts, pools, apps, rsync_tasks, snapshot_tasks, disks = await asyncio.gather(
                self.client.system_info(),
                # None (not []) when denied, so a denial isn't mistaken for every alert clearing.
                self._optional("alert.list", self.client.alerts, None),
                self._optional("pool.query", self.client.pools, {}),
                self._optional("app.query", self.client.apps, {}),
                self._optional("rsynctask.query", self.client.rsync_tasks, {}),
                self._optional("pool.snapshottask.query", self.client.snapshot_tasks, {}),
                self._fetch_disks(),
            )
        except TrueNASAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TrueNASError as err:
            raise UpdateFailed(str(err)) from err

        self._stabilize_boot_time(system)
        self._track_alerts(alerts, system)
        return TrueNASData(
            system=system,
            stats=self.client.realtime_stats(),
            alerts=alerts or [],
            pools=pools,
            apps=apps,
            rsync_tasks=rsync_tasks,
            snapshot_tasks=snapshot_tasks,
            disks=disks,
        )

    async def _fetch_disks(self) -> dict[str, Disk]:
        """Disks with their temperatures, refreshed at most every DISK_INTERVAL.

        Best effort: a failing source (e.g. disk.temperatures timing out on slow SMART reads) must neither fail the
        whole update nor be retried every scan, so the attempt time is recorded before fetching.
        """
        now = time.monotonic()
        if self._disks_at is not None and now - self._disks_at < DISK_INTERVAL.total_seconds():
            return self._disks
        self._disks_at = now
        disks, temperatures = await asyncio.gather(
            self._best_effort("disk.query", self.client.disks),
            self._best_effort("disk.temperatures", self.client.disk_temperatures),
        )
        # Keep the known disks if disk.query failed, so their entities don't flap to unavailable.
        disks = self._disks if disks is None else disks
        for disk in disks.values():
            disk.temperature = (temperatures or {}).get(disk.name)
        self._disks = disks
        return disks

    async def _best_effort[T](self, method: str, fetch: Callable[[], Awaitable[T]]) -> T | None:
        """Like `_optional`, but any non-auth error also yields None (logged once until the call succeeds again)."""
        try:
            result = await self._optional(method, fetch, None)
        except TrueNASAuthError:
            raise
        except TrueNASError as err:
            if method not in self._disk_errors:
                self._disk_errors.add(method)
                LOGGER.warning("Calling %s failed, retrying every %s: %s", method, DISK_INTERVAL, err)
            return None
        self._disk_errors.discard(method)
        return result

    def _track_alerts(self, alerts: list[Alert] | None, system: SystemInfo) -> None:
        """Fire EVENT_ALERT for alerts raised/cleared since the last known state.

        The baseline is only (re)seeded, never diffed, when it is unknown: on the first refresh and after
        alert.list was denied, so pre-existing alerts don't fire as a burst of "raised" events.
        """
        if alerts is None:
            self._alert_baseline = None
            return
        before = self._alert_baseline
        after = {a.uuid: a for a in alerts if not a.dismissed}
        self._alert_baseline = after
        if before is None:
            return
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
