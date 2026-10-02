"""Minimal async JSON-RPC 2.0 WebSocket client for the TrueNAS API (25.04+)."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import Counter, deque
from typing import Any

import aiohttp

from .exceptions import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASMethodNotFoundError,
    TrueNASPermissionError,
)
from .models import (
    Alert,
    App,
    AppStats,
    AppStatsSample,
    Disk,
    Pool,
    RealtimeSample,
    Service,
    Stats,
    SystemInfo,
    Task,
    UpdateInfo,
    _number,
)

_LOGGER = logging.getLogger(__name__)

API_PATH = "/api/current"
REALTIME_COLLECTION = "reporting.realtime"
APP_STATS_COLLECTION = "app.stats"
# Both feeds publish every ~2s; anything older means the feed stalled.
REALTIME_MAX_AGE = 60
# Samples kept between reads (10 min at 2s), so a slow update interval still averages over all of them.
WINDOW_SIZE = 300
# JSON-RPC 2.0 "Method not found": the method doesn't exist on this TrueNAS release.
METHOD_NOT_FOUND = -32601


class TrueNASClient:
    """Read-only client over a single persistent, authenticated WebSocket.

    Always uses wss://: TrueNAS revokes API keys that are sent over plain ws://.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        api_key: str,
        port: int = 443,
        *,
        timeout: float = 15,
        subscribe_realtime: bool = True,
        ws_url: str | None = None,
    ) -> None:
        """Initialize. `ws_url` overrides the computed URL and exists for tests."""
        self._session = session
        self._api_key = api_key
        self._timeout = timeout
        self._subscribe_realtime = subscribe_realtime
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # IPv6 literal
        self.url = ws_url or f"wss://{host}:{port}{API_PATH}"

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._authenticated = False
        self._reader: asyncio.Task[None] | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._connect_lock = asyncio.Lock()
        # Newest full reporting.realtime frame (gauges), plus compact samples of all frames since the last read.
        self._realtime: dict[str, Any] | None = None
        self._realtime_at = 0.0
        self._realtime_window: deque[RealtimeSample] = deque(maxlen=WINDOW_SIZE)
        self._app_stats_at = 0.0
        self._app_stats_window: deque[AppStatsSample] = deque(maxlen=WINDOW_SIZE)
        # Newest app.stats sample, standing in for the window when no frame arrived since the last read.
        self._app_stats_last: AppStatsSample | None = None
        # Subscription ids by collection, while subscribed (the server may drop one with notify_unsubscribed).
        self._subscriptions: dict[str, str] = {}
        # Collections whose last subscribe attempt failed, so the failure is only warned about once.
        self._subscribe_failed: set[str] = set()
        # 25.04 only has update.check_available, 25.10+ only update.status; tried first once one worked.
        self._legacy_update = False

    @property
    def connected(self) -> bool:
        """True once the socket is open *and* login succeeded, while its reader is still running."""
        return (
            self._authenticated
            and self._ws is not None
            and not self._ws.closed
            and self._reader is not None
            and not self._reader.done()
        )

    async def connect(self) -> None:
        """Open the socket, authenticate and (optionally) subscribe to realtime stats."""
        async with self._connect_lock:
            if self.connected:
                return
            # Drop what is left of a previous connection, e.g. a socket still open after its reader stopped.
            await self._close_socket()
            try:
                async with asyncio.timeout(self._timeout):
                    self._ws = await self._session.ws_connect(self.url, heartbeat=30)
            except (aiohttp.ClientError, TimeoutError, OSError) as err:
                raise TrueNASConnectionError(f"Cannot connect to {self.url}: {err}") from err

            self._reader = asyncio.create_task(self._read_loop())
            try:
                if not await self._call("auth.login_with_api_key", [self._api_key]):
                    raise TrueNASAuthError("API key rejected")
                self._authenticated = True
                if self._subscribe_realtime:
                    await self._subscribe(REALTIME_COLLECTION)
                    await self._subscribe(APP_STATS_COLLECTION)
            except BaseException:
                await self._close_socket()
                raise

    async def _subscribe(self, collection: str) -> None:
        """Best effort: e.g. app.stats fails while apps (Docker) aren't running, or the key may lack the role.

        Failures are warned about once until a subscription succeeds again; `resubscribe()` retries.
        """
        try:
            sub = await self._call("core.subscribe", [collection])
        except (TrueNASAuthError, TrueNASConnectionError):
            raise
        except TrueNASError as err:
            if collection not in self._subscribe_failed:
                self._subscribe_failed.add(collection)
                _LOGGER.warning("Cannot subscribe to %s; related sensors stay unavailable: %s", collection, err)
            return
        if collection in self._subscribe_failed:
            self._subscribe_failed.discard(collection)
            _LOGGER.info("Subscribed to %s again", collection)
        if isinstance(sub, str):
            self._subscriptions[collection] = sub

    async def resubscribe(self, collection: str) -> None:
        """Subscribe to a collection again, e.g. app.stats once apps were started after connecting."""
        if not self.connected:
            await self.connect()  # Subscribes on its own.
            return
        if (sub := self._subscriptions.pop(collection, None)) is not None:
            # TrueNAS sends notify_unsubscribed for it before replying, so no late notice can hit the new one.
            try:
                await self._call("core.unsubscribe", [sub])
            except (TrueNASAuthError, TrueNASConnectionError):
                raise
            except TrueNASError:
                pass  # Already gone on the server side.
        await self._subscribe(collection)

    async def close(self) -> None:
        async with self._connect_lock:
            await self._close_socket()

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Call a JSON-RPC method, connecting first if needed."""
        if not self.connected:
            await self.connect()
        return await self._call(method, params or [])

    # Typed helpers ---------------------------------------------------------

    async def host_id(self) -> str:
        return await self.call("system.host_id")

    async def system_info(self) -> SystemInfo:
        return SystemInfo.from_api(await self.call("system.info"))

    async def alerts(self) -> list[Alert]:
        return [Alert.from_api(a) for a in await self.call("alert.list")]

    async def pools(self) -> dict[str, Pool]:
        pools = (Pool.from_api(p) for p in await self.call("pool.query"))
        return {p.name: p for p in pools}

    async def apps(self) -> dict[str, App]:
        apps = (App.from_api(a) for a in await self.call("app.query"))
        return {a.name: a for a in apps}

    async def rsync_tasks(self) -> dict[str, Task]:
        return {str(t["id"]): Task.from_rsync(t) for t in await self.call("rsynctask.query")}

    async def snapshot_tasks(self) -> dict[str, Task]:
        return {str(t["id"]): Task.from_snapshot(t) for t in await self.call("pool.snapshottask.query")}

    async def disks(self) -> dict[str, Disk]:
        """Disks keyed by `Disk.key`; `extra.pools` fills in the pool each disk belongs to."""
        disks = [Disk.from_api(d) for d in await self.call("disk.query", [[], {"extra": {"pools": True}}])]
        # Some USB/SATA bridges report one serial for every disk behind them; tell those apart by name rather than
        # letting one silently replace the other.
        keys = Counter(d.key for d in disks)
        return {d.key if keys[d.key] == 1 else f"{d.key}_{d.name}": d for d in disks}

    async def disk_temperatures(self) -> dict[str, float]:
        """Temperatures in °C keyed by disk name (sda, ...); disks that can't report one are left out."""
        temps = await self.call("disk.temperatures", [[]])
        if not isinstance(temps, dict):
            return {}
        return {name: t for name, t in temps.items() if _number(t) is not None}

    async def services(self) -> dict[str, Service]:
        services = (Service.from_api(s) for s in await self.call("service.query"))
        return {s.name: s for s in services}

    async def update_info(self) -> UpdateInfo:
        """update.status on 25.10+; 25.04 only has update.check_available, which asks the iX update server.

        The method that worked last is tried first, and the other one whenever it is missing, so upgrading from
        25.04 to 25.10 (or anything else that swaps them) needs no reconnect.
        """
        methods = ["update.status", "update.check_available"]
        if self._legacy_update:
            methods.reverse()
        for method in methods:
            try:
                result = await self.call(method)
            except TrueNASMethodNotFoundError:
                if method == methods[-1]:
                    raise
                continue
            self._legacy_update = method == "update.check_available"
            if self._legacy_update:
                return UpdateInfo.from_check_available(result if isinstance(result, dict) else {})
            if not isinstance(result, dict):
                raise TrueNASError("Malformed update.status response")
            if result.get("code") == "ERROR":
                error = result.get("error")
                raise TrueNASError((isinstance(error, dict) and error.get("reason")) or "Update check failed")
            if not isinstance(result.get("status"), dict):
                # Nothing known yet (e.g. right after boot): unknown, not "up to date".
                raise TrueNASError("TrueNAS has not checked for updates yet")
            return UpdateInfo.from_status(result)
        raise AssertionError("unreachable")

    def realtime_stats(self, previous_arc_hit_ratio: float | None = None) -> Stats | None:
        """reporting.realtime frames received since the previous call, or None if missing or stale.

        Consumes the frames: rates are averaged over everything since the last read.
        """
        window = list(self._realtime_window)
        self._realtime_window.clear()
        if self._realtime is None or time.monotonic() - self._realtime_at > REALTIME_MAX_AGE:
            return None
        return Stats.from_realtime(self._realtime, window, previous_arc_hit_ratio)

    def app_stats(self) -> dict[str, AppStats] | None:
        """Per-app usage from the app.stats frames since the previous call (consumed), or None if missing or stale."""
        window = list(self._app_stats_window)
        self._app_stats_window.clear()
        if self._app_stats_last is None or time.monotonic() - self._app_stats_at > REALTIME_MAX_AGE:
            return None
        return AppStats.from_samples(window or [self._app_stats_last])

    # Internals -------------------------------------------------------------

    async def _call(self, method: str, params: list[Any]) -> Any:
        if self._ws is None or self._ws.closed:
            raise TrueNASConnectionError("Not connected")
        msg_id = next(self._ids)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send_json({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            async with asyncio.timeout(self._timeout):
                return await future
        except TimeoutError as err:
            raise TrueNASConnectionError(f"Timeout calling {method}") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise TrueNASConnectionError(f"Error calling {method}: {err}") from err
        finally:
            self._pending.pop(msg_id, None)

    async def _read_loop(self) -> None:
        ws = self._ws
        assert ws is not None
        try:
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    try:
                        self._handle(msg.json())
                    except ValueError:
                        _LOGGER.debug("Ignoring non-JSON message: %s", msg.data)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        except Exception:
            # The stopped reader makes `connected` false, so the next call reconnects.
            _LOGGER.exception("WebSocket reader failed; reconnecting on next call")
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(TrueNASConnectionError("Connection closed"))
            self._pending.clear()

    def _handle(self, msg: Any) -> None:
        # Anything malformed is dropped here: an exception would stop the reader and with it the connection.
        if not isinstance(msg, dict):
            _LOGGER.debug("Ignoring non-object message: %s", msg)
            return
        msg_id = msg.get("id")
        if msg_id is not None:
            future = self._pending.get(msg_id) if isinstance(msg_id, int) else None
            if future is None or future.done():
                return
            if (error := msg.get("error")) is not None:
                future.set_exception(_map_error(error))
            else:
                future.set_result(msg.get("result"))
            return

        params = msg.get("params")
        if not isinstance(params, dict):
            return
        collection = params.get("collection")
        if msg.get("method") == "collection_update":
            fields = params.get("fields")
            if collection == REALTIME_COLLECTION and isinstance(fields, dict) and fields:
                self._realtime = fields
                self._realtime_at = time.monotonic()
                self._realtime_window.append(RealtimeSample.from_realtime(fields))
            elif collection == APP_STATS_COLLECTION and isinstance(fields, list):
                self._app_stats_last = AppStats.sample(fields)
                self._app_stats_at = time.monotonic()
                self._app_stats_window.append(self._app_stats_last)
        elif msg.get("method") == "notify_unsubscribed" and isinstance(collection, str):
            # The event source stopped (e.g. Docker went down); the coordinator subscribes again once it's stale.
            self._subscriptions.pop(collection, None)

    async def _close_socket(self) -> None:
        self._authenticated = False
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)
        self._ws = None
        self._reader = None
        self._realtime = None
        self._realtime_window.clear()
        self._app_stats_last = None
        self._app_stats_window.clear()
        self._subscriptions.clear()


def _map_error(error: Any) -> TrueNASError:
    if not isinstance(error, dict):
        return TrueNASError(str(error))
    if error.get("code") == METHOD_NOT_FOUND:
        return TrueNASMethodNotFoundError(error.get("message") or "Method not found")
    data = error.get("data")
    if not isinstance(data, dict):
        data = {}
    errname = data.get("errname")
    reason = data.get("reason") or error.get("message") or "Unknown error"
    if errname == "ENOTAUTHENTICATED":
        return TrueNASAuthError(reason)
    if errname == "EACCES":
        return TrueNASPermissionError(reason)
    return TrueNASError(reason)
