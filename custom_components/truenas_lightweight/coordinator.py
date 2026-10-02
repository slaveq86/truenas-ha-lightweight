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
    App,
    Disk,
    SystemInfo,
    TrueNASAuthError,
    TrueNASClient,
    TrueNASData,
    TrueNASError,
    TrueNASPermissionError,
    UpdateInfo,
)
from .api.client import APP_STATS_COLLECTION, REALTIME_COLLECTION
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DISK_INTERVAL,
    DOMAIN,
    EVENT_ALERT,
    LOGGER,
    RESUBSCRIBE_INTERVAL,
    UPDATE_INTERVAL,
    UPDATE_RETRY,
)

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
        # Best-effort sources currently failing, so each failure is logged once.
        self._errors: set[str] = set()
        # The update check runs in the background (it may wait on the iX update server); see `_schedule_update_check`.
        self._update: UpdateInfo | None = None
        self._update_due = 0.0
        self._update_version: str | None = None
        self._update_task: asyncio.Task[None] | None = None
        # time.monotonic() of the last (re)subscribe per push collection. The client subscribes on connect, so the
        # first retry waits a full interval for that subscription to deliver.
        self._subscribed_at = dict.fromkeys((REALTIME_COLLECTION, APP_STATS_COLLECTION), time.monotonic())
        # Kept across refreshes: an idle ARC (no reads) has no hit ratio of its own.
        self._arc_hit_ratio: float | None = None

    async def _async_update_data(self) -> TrueNASData:
        try:
            system, alerts, pools, apps, rsync_tasks, snapshot_tasks, disks, services = await asyncio.gather(
                self.client.system_info(),
                # None (not []) when denied, so a denial isn't mistaken for every alert clearing.
                self._optional("alert.list", self.client.alerts, None),
                self._optional("pool.query", self.client.pools, {}),
                self._optional("app.query", self.client.apps, {}),
                self._optional("rsynctask.query", self.client.rsync_tasks, {}),
                self._optional("pool.snapshottask.query", self.client.snapshot_tasks, {}),
                self._fetch_disks(),
                self._optional("service.query", self.client.services, {}),
            )
            stats = self.client.realtime_stats(self._arc_hit_ratio)
            if stats is not None:
                self._arc_hit_ratio = stats.arc_hit_ratio
            await self._resubscribe_if_silent(REALTIME_COLLECTION, stats is None)
            await self._merge_app_stats(apps)
        except TrueNASAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except TrueNASError as err:
            raise UpdateFailed(str(err)) from err

        self._stabilize_boot_time(system)
        self._track_alerts(alerts, system)
        self._schedule_update_check(system.version)
        return TrueNASData(
            system=system,
            stats=stats,
            alerts=alerts or [],
            pools=pools,
            apps=apps,
            rsync_tasks=rsync_tasks,
            snapshot_tasks=snapshot_tasks,
            disks=disks,
            services=services,
            update=self._update,
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
            self._best_effort("disk.query", self.client.disks, DISK_INTERVAL),
            self._best_effort("disk.temperatures", self.client.disk_temperatures, DISK_INTERVAL),
        )
        # Keep the known disks if disk.query failed, so their entities don't flap to unavailable.
        disks = self._disks if disks is None else disks
        for disk in disks.values():
            disk.temperature = (temperatures or {}).get(disk.name)
        self._disks = disks
        return disks

    def _schedule_update_check(self, version: str) -> None:
        """Start a background update check when due: every UPDATE_INTERVAL, after a failure every UPDATE_RETRY, and
        right away once the installed version changed. Its result reaches the entities when it finishes, so a slow
        update server never holds up a refresh (or setup)."""
        if self._update_task is not None and not self._update_task.done():
            return
        if version == self._update_version and time.monotonic() < self._update_due:
            return
        self._update_version = version
        # Provisional: replaced by UPDATE_INTERVAL once the check succeeds.
        self._update_due = time.monotonic() + UPDATE_RETRY.total_seconds()
        self._update_task = self.config_entry.async_create_background_task(
            self.hass, self._check_update(), f"{DOMAIN} update check"
        )

    async def _check_update(self) -> None:
        try:
            update = await self._best_effort("update.status", self.client.update_info, UPDATE_RETRY)
        except TrueNASAuthError:
            return  # The next regular refresh runs into it too and starts reauth.
        if update is None:
            return  # Failed or denied; the last known result stays.
        self._update = update
        self._update_due = time.monotonic() + UPDATE_INTERVAL.total_seconds()
        if self.data is not None:
            self.data.update = update
            self.async_update_listeners()

    async def _merge_app_stats(self, apps: dict[str, App]) -> None:
        """Copy app.stats usage onto the apps; resubscribe (throttled) if running apps get no stats."""
        stats = self.client.app_stats()
        for name, app in apps.items():
            if stats is not None and (usage := stats.get(name)) is not None:
                app.cpu_usage, app.memory = usage.cpu_usage, usage.memory
        running = any(app.state == "RUNNING" for app in apps.values())
        await self._resubscribe_if_silent(APP_STATS_COLLECTION, stats is None and running)

    async def _resubscribe_if_silent(self, collection: str, silent: bool) -> None:
        """Subscribe to a push feed again, at most every RESUBSCRIBE_INTERVAL, while it delivers nothing.

        The client warns about failed subscriptions itself (once per failure streak).
        """
        now = time.monotonic()
        if not silent or now - self._subscribed_at[collection] < RESUBSCRIBE_INTERVAL.total_seconds():
            return
        self._subscribed_at[collection] = now
        try:
            await self.client.resubscribe(collection)
        except TrueNASAuthError:
            raise
        except TrueNASError as err:
            LOGGER.debug("Subscribing to %s again failed: %s", collection, err)

    async def _best_effort[T](self, method: str, fetch: Callable[[], Awaitable[T]], interval: timedelta) -> T | None:
        """Like `_optional`, but any non-auth error also yields None (logged once until the call succeeds again)."""
        try:
            result = await self._optional(method, fetch, None)
        except TrueNASAuthError:
            raise
        except TrueNASError as err:
            if method not in self._errors:
                self._errors.add(method)
                LOGGER.warning("Calling %s failed, retrying every %s: %s", method, interval, err)
            return None
        self._errors.discard(method)
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
