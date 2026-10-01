"""Minimal async JSON-RPC 2.0 WebSocket client for the TrueNAS API (25.04+)."""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import Counter
from typing import Any

import aiohttp

from .exceptions import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASPermissionError,
)
from .models import Alert, App, Disk, Pool, Stats, SystemInfo, Task

_LOGGER = logging.getLogger(__name__)

API_PATH = "/api/current"
REALTIME_COLLECTION = "reporting.realtime"
# reporting.realtime publishes every ~2s; anything older means the feed stalled.
REALTIME_MAX_AGE = 60


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
        self._realtime: dict[str, Any] | None = None
        self._realtime_at = 0.0

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
                    try:
                        await self._call("core.subscribe", [REALTIME_COLLECTION])
                    except TrueNASPermissionError:
                        _LOGGER.warning("API key may not read %s; CPU/memory sensors disabled", REALTIME_COLLECTION)
            except BaseException:
                await self._close_socket()
                raise

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
        return {name: t for name, t in temps.items() if isinstance(t, int | float) and not isinstance(t, bool)}

    def realtime_stats(self) -> Stats | None:
        """Latest reporting.realtime snapshot, or None if missing or stale."""
        if self._realtime is None or time.monotonic() - self._realtime_at > REALTIME_MAX_AGE:
            return None
        return Stats.from_realtime(self._realtime)

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

        if msg.get("method") == "collection_update":
            params = msg.get("params")
            if not isinstance(params, dict):
                return
            fields = params.get("fields")
            if params.get("collection") == REALTIME_COLLECTION and isinstance(fields, dict) and fields:
                self._realtime = fields
                self._realtime_at = time.monotonic()

    async def _close_socket(self) -> None:
        self._authenticated = False
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            await asyncio.gather(self._reader, return_exceptions=True)
        self._ws = None
        self._reader = None
        self._realtime = None


def _map_error(error: Any) -> TrueNASError:
    if not isinstance(error, dict):
        return TrueNASError(str(error))
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
