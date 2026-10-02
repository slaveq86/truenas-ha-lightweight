"""Tests for the JSON-RPC WebSocket client against a fake TrueNAS server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.truenas_lightweight.api import (
    TrueNASAuthError,
    TrueNASClient,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASMethodNotFoundError,
    TrueNASPermissionError,
)
from custom_components.truenas_lightweight.api.models import AppStats, Stats, SystemInfo, Task, UpdateInfo

from .conftest import load_fixture

VALID_KEY = "1-good"


class FakeTrueNAS:
    """Minimal JSON-RPC 2.0 server mimicking /api/current."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.denied: set[str] = set()
        # Collections core.subscribe refuses (EACCES).
        self.denied_collections: set[str] = set()
        self.login_delay = 0.0
        self.login_started = asyncio.Event()
        # Frames sent ahead of the next reply, then cleared.
        self.junk: list[Any] = []
        # Methods answered with this raw (possibly malformed) JSON-RPC "error" value.
        self.raw_errors: dict[str, Any] = {}
        self.responses: dict[str, Any] = {
            "system.info": load_fixture("system_info.json"),
            "system.host_id": "hostid",
            "alert.list": load_fixture("alert_list.json"),
            "pool.query": load_fixture("pool_query.json"),
            "app.query": load_fixture("app_query.json"),
            "rsynctask.query": load_fixture("rsynctask_query.json"),
            "pool.snapshottask.query": load_fixture("snapshottask_query.json"),
            "disk.query": load_fixture("disk_query.json"),
            "disk.temperatures": load_fixture("disk_temperatures.json"),
            "service.query": load_fixture("service_query.json"),
            # 25.04: no update.status, which is answered with "method not found".
            "update.check_available": load_fixture("update_check_available.json"),
            "core.unsubscribe": None,
        }
        self.events: dict[str, Any] = {
            "reporting.realtime": load_fixture("reporting_realtime.json"),
            "app.stats": load_fixture("app_stats.json"),
        }

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        authenticated = False
        async for msg in ws:
            req = msg.json()
            method, params, msg_id = req["method"], req["params"], req["id"]
            self.calls.append(method)
            for frame in self.junk:
                await ws.send_json(frame)
            self.junk = []
            if method == "auth.login_with_api_key":
                self.login_started.set()
                await asyncio.sleep(self.login_delay)
                authenticated = params == [VALID_KEY]
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": authenticated})
            elif not authenticated:
                await ws.send_json(_error(msg_id, "ENOTAUTHENTICATED", "Not authenticated"))
            elif method in self.denied:
                await ws.send_json(_error(msg_id, "EACCES", "Not authorized"))
            elif method in self.raw_errors:
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "error": self.raw_errors[method]})
            elif method == "core.subscribe" and params[0] in self.denied_collections:
                await ws.send_json(_error(msg_id, "EACCES", "Not authorized"))
            elif method == "core.subscribe":
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": f"sub-{params[0]}"})
                await ws.send_json(event(params[0], self.events[params[0]]))
            elif method in self.responses:
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": self.responses[method]})
            else:
                await ws.send_json(
                    {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
                )
        return ws


def event(collection: str, fields: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "collection_update",
        "params": {"msg": "added", "collection": collection, "fields": fields},
    }


def _error(msg_id: int, errname: str, reason: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32001, "message": "Method call error", "data": {"errname": errname, "reason": reason}},
    }


@pytest.fixture
async def fake(socket_enabled: None) -> AsyncGenerator[tuple[FakeTrueNAS, str]]:
    fake = FakeTrueNAS()
    app = web.Application()
    app.router.add_get("/api/current", fake.handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    yield fake, str(server.make_url("/api/current")).replace("http://", "ws://")
    await server.close()


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as session:
        yield session


async def test_fetch_all(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)

    info, pools, apps, alerts, rsync, snapshots, disks, temps = await asyncio.gather(
        client.system_info(),
        client.pools(),
        client.apps(),
        client.alerts(),
        client.rsync_tasks(),
        client.snapshot_tasks(),
        client.disks(),
        client.disk_temperatures(),
    )

    assert info.hostname == "truenas"
    assert info.loadavg == (0.42, 0.37, 0.31)
    assert pools["backup"].healthy is False
    assert pools["tank"].used_pct == 81.0
    assert apps["plex"].upgrade_available is True
    assert [a.dismissed for a in alerts] == [False, False, True]
    assert {k: t.state for k, t in rsync.items()} == {"1": "SUCCESS", "2": "FAILED", "3": "PENDING"}
    assert {k: t.state for k, t in snapshots.items()} == {"1": "SUCCESS", "2": "FAILED", "3": "HOLD"}
    # Keyed by serial; a disk without one falls back to its identifier.
    assert disks["WD-WCC7K1ABCDEF"].pool == "tank"
    assert disks["{devicename}sdd"].serial is None
    assert disks["S4EWNX0R123456"].type == "SSD"
    # Disks that can't report a temperature (null) are dropped.
    assert temps == {"sda": 34, "sdb": 36, "sdc": 52, "nvme0n1": 45}
    # Concurrent calls must share a single login.
    assert server.calls.count("auth.login_with_api_key") == 1

    stats = client.realtime_stats()
    assert stats is not None
    assert stats.cpu_usage == 12.0
    assert stats.mem_used_pct == 75.0
    await client.close()
    assert not client.connected


async def test_disks_sharing_a_serial(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    bridge = {"serial": "000000000001", "identifier": "{serial}000000000001", "model": "USB3.0 bridge"}
    server.responses["disk.query"] = [
        *load_fixture("disk_query.json")[:1],
        {"name": "sde", **bridge},
        {"name": "sdf", **bridge},
    ]
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    disks = await client.disks()
    assert set(disks) == {"WD-WCC7K1ABCDEF", "000000000001_sde", "000000000001_sdf"}
    await client.close()


async def test_default_url_is_wss(session: aiohttp.ClientSession) -> None:
    client = TrueNASClient(session, "nas.local", "key", 8443)
    assert client.url == "wss://nas.local:8443/api/current"


async def test_ipv6_url(session: aiohttp.ClientSession) -> None:
    client = TrueNASClient(session, "fd00::10", "key")
    assert client.url == "wss://[fd00::10]:443/api/current"


async def test_not_connected_until_logged_in(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """A call arriving while login is in flight must wait instead of going out unauthenticated."""
    server, url = fake
    server.login_delay = 0.2
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    connecting = asyncio.create_task(client.connect())
    await server.login_started.wait()
    assert not client.connected

    assert (await client.host_id()) == "hostid"
    await connecting
    assert server.calls.count("auth.login_with_api_key") == 1
    await client.close()


async def test_invalid_key(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    _, url = fake
    client = TrueNASClient(session, "unused", "bad", ws_url=url)
    with pytest.raises(TrueNASAuthError):
        await client.system_info()
    assert not client.connected


async def test_permission_denied(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    server.denied = {"app.query", "core.subscribe"}
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    # A denied realtime subscription doesn't prevent connecting.
    assert (await client.system_info()).version == "25.04.2"
    assert client.realtime_stats() is None
    with pytest.raises(TrueNASPermissionError):
        await client.apps()
    await client.close()


async def test_unknown_method(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    _, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    with pytest.raises(TrueNASError, match="Method not found"):
        await client.call("does.not.exist")
    await client.close()


async def test_cannot_connect(socket_enabled: None, session: aiohttp.ClientSession) -> None:
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url="ws://127.0.0.1:1/api/current", timeout=2)
    with pytest.raises(TrueNASConnectionError):
        await client.connect()


async def test_reconnects_after_drop(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    assert client._ws is not None
    await client._ws.close()

    assert (await client.host_id()) == "hostid"
    assert server.calls.count("auth.login_with_api_key") == 2
    await client.close()


async def test_ignores_malformed_messages(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """Malformed frames must not stop the reader, which would leave every later call to time out."""
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url, timeout=2)
    await client.connect()
    stats = client.realtime_stats()
    server.junk = [
        [],
        "text",
        {"jsonrpc": "2.0", "id": [1], "result": "unhashable id"},
        {"jsonrpc": "2.0", "method": "collection_update", "params": ["reporting.realtime"]},
        {"jsonrpc": "2.0", "method": "collection_update", "params": {"collection": "reporting.realtime", "fields": 1}},
    ]
    server.raw_errors = {"system.info": "boom", "app.query": {"message": "bad", "data": "not a dict"}}

    assert (await client.host_id()) == "hostid"
    with pytest.raises(TrueNASError, match="boom"):
        await client.system_info()
    with pytest.raises(TrueNASError, match="bad"):
        await client.apps()
    assert client.connected
    assert client.realtime_stats() == stats
    assert server.calls.count("auth.login_with_api_key") == 1
    await client.close()


async def test_reconnects_after_reader_failure(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """If the reader dies anyway, the still-open socket is dropped and the next call reconnects."""
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url, timeout=2)
    await client.connect()
    old_ws = client._ws
    assert old_ws is not None

    with patch.object(client, "_handle", side_effect=RuntimeError("bug")), pytest.raises(TrueNASConnectionError):
        await client.host_id()
    assert not client.connected

    assert (await client.host_id()) == "hostid"
    assert server.calls.count("auth.login_with_api_key") == 2
    assert old_ws.closed
    await client.close()


def test_model_ignores_cpu_model() -> None:
    """system.info "model" is the CPU; only system_product describes the hardware."""
    info = SystemInfo.from_api({"model": "AMD Ryzen 5 5600G"})
    assert info.model is None
    assert SystemInfo.from_api({"model": "AMD", "system_product": "TRUENAS-MINI-3.0-X+"}).model == "TRUENAS-MINI-3.0-X+"


def test_task_prefers_job_over_state() -> None:
    task = Task.from_rsync(
        {
            "id": 7,
            "path": "/mnt/tank/x",
            "job": {"state": "RUNNING", "time_started": {"$date": 1759300000000}},
            "state": {"state": "FAILED", "error": "old"},
        }
    )
    assert task.state == "RUNNING"
    assert task.error is None
    assert task.last_run is not None
    assert task.name == "/mnt/tank/x"


def test_task_parsing() -> None:
    never_run = Task.from_rsync({"id": 3, "desc": "Media", "job": None})
    assert (never_run.state, never_run.last_run, never_run.problem) == ("PENDING", None, False)

    weekly, daily, hourly = (Task.from_snapshot(t) for t in load_fixture("snapshottask_query.json"))
    assert (daily.state, daily.error, daily.problem) == ("FAILED", "dataset is busy", True)
    assert daily.details["lifetime"] == "1 day"
    # Several tasks on one dataset are told apart by retention.
    assert weekly.name == "tank/photos (recursive, 2 weeks)"
    assert hourly.name == "tank/photos (recursive, 48 hours)"
    assert daily.name == "tank/vms (1 day)"
    # HOLD is a problem and its "reason" is surfaced as the error.
    assert (hourly.state, hourly.error, hourly.problem) == ("HOLD", "Dataset tank/photos is locked", True)


def test_snapshot_task_tolerates_missing_lifetime() -> None:
    task = Task.from_snapshot({"id": 4, "dataset": "tank/x", "lifetime_value": 2, "lifetime_unit": None})
    assert (task.name, task.details["lifetime"]) == ("tank/x", None)


async def test_services_and_update_fallback(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)

    services = await client.services()
    assert (services["cifs"].enabled, services["cifs"].running) == (True, True)
    assert (services["nfs"].enabled, services["nfs"].running) == (True, False)

    # 25.04 doesn't know update.status; the client falls back once and stays on check_available.
    for _ in range(2):
        update = await client.update_info()
        assert update == UpdateInfo(
            True,
            "25.04.3",
            "https://www.truenas.com/docs/scale/25.04/gettingstarted/scalereleasenotes/#25043-changelog",
        )
    assert server.calls.count("update.status") == 1
    assert server.calls.count("update.check_available") == 2
    await client.close()


async def test_update_after_upgrade(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """After a 25.04 -> 25.10 upgrade (no update.check_available any more) the client switches to update.status."""
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    assert (await client.update_info()).version == "25.04.3"

    del server.responses["update.check_available"]
    server.responses["update.status"] = load_fixture("update_status.json")
    assert (await client.update_info()).version == "25.10.1"
    assert (await client.update_info()).version == "25.10.1"
    assert server.calls.count("update.check_available") == 2
    assert server.calls.count("update.status") == 3

    # Neither method: the error surfaces instead of looping.
    del server.responses["update.status"]
    with pytest.raises(TrueNASMethodNotFoundError):
        await client.update_info()
    await client.close()


async def test_update_status(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    server.responses["update.status"] = load_fixture("update_status.json")
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)

    update = await client.update_info()
    assert (update.available, update.version) == (True, "25.10.1")
    assert "update.check_available" not in server.calls

    # No check done yet (e.g. right after boot) is unknown, not "up to date".
    server.responses["update.status"] = {**load_fixture("update_status.json"), "status": None}
    with pytest.raises(TrueNASError, match="not checked for updates yet"):
        await client.update_info()

    server.responses["update.status"]["code"] = "ERROR"
    server.responses["update.status"]["error"] = {"errname": "ENONET", "reason": "Unable to reach update server"}
    with pytest.raises(TrueNASError, match="Unable to reach update server"):
        await client.update_info()
    await client.close()


async def test_method_not_found(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    _, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    with pytest.raises(TrueNASMethodNotFoundError):
        await client.call("update.status")
    await client.close()


async def test_realtime_window(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """Rates are averaged over every frame since the last read; gauges come from the newest frame."""
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    frame = load_fixture("reporting_realtime.json")
    later = {
        **frame,
        "cpu": {"cpu": {"usage": 30.0, "temp": 50.0}},
        "disks": {**frame["disks"], "read_bytes": 0.0},
        "interfaces": {"enp5s0": {**frame["interfaces"]["enp5s0"], "received_bytes_rate": 2500000.0}},
    }
    server.junk = [event("reporting.realtime", later)]
    await client.host_id()

    stats = client.realtime_stats()
    assert stats is not None
    assert stats.cpu_usage == 21.0  # (12.04 + 30) / 2
    assert stats.cpu_temp == 50.0
    assert stats.disk_read_rate == 5242880
    assert stats.interfaces["enp5s0"].rx_rate == 1875000
    # The newest frame no longer lists enp6s0.
    assert set(stats.interfaces) == {"enp5s0"}

    # The window was consumed; without new frames the latest one stands alone.
    assert client.realtime_stats().cpu_usage == 30.0
    await client.close()


async def test_app_stats(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url, timeout=2)
    await client.connect()
    await client.host_id()  # Let the subscription's first frame arrive.

    assert client.app_stats() == {"plex": AppStats(3.5, 536870912), "nextcloud": AppStats(0, 0)}

    server.junk = [
        event("app.stats", {"app_name": "plex"}),
        event("app.stats", [{"app_name": 1}, "junk", {"app_name": "plex", "cpu_usage": 6.5, "memory": "x"}]),
        {"jsonrpc": "2.0", "method": "notify_unsubscribed", "params": {"collection": "app.stats", "error": None}},
    ]
    await client.host_id()
    assert client.connected
    assert client.app_stats() == {"plex": AppStats(6.5, None)}
    assert "app.stats" not in client._subscriptions
    assert client._subscriptions == {"reporting.realtime": "sub-reporting.realtime"}

    # The server dropped the subscription: no unsubscribe needed before subscribing again.
    await client.resubscribe("app.stats")
    assert "core.unsubscribe" not in server.calls
    assert client._subscriptions["app.stats"] == "sub-app.stats"
    await client.resubscribe("app.stats")
    assert server.calls.count("core.unsubscribe") == 1
    assert server.calls.count("core.subscribe") == 4
    await client.close()


async def test_resubscribe_reconnects(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.resubscribe("reporting.realtime")
    # Connecting subscribes to both feeds; nothing is subscribed twice.
    assert server.calls.count("core.subscribe") == 2
    await client.close()


async def test_subscribe_failure_warned_once(
    fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession, caplog: pytest.LogCaptureFixture
) -> None:
    server, url = fake
    server.denied_collections = {"app.stats"}
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    await client.resubscribe("app.stats")
    await client.resubscribe("app.stats")
    assert caplog.text.count("Cannot subscribe to app.stats") == 1

    server.denied_collections = set()
    await client.resubscribe("app.stats")
    assert "Subscribed to app.stats again" in caplog.text
    assert client._subscriptions["app.stats"] == "sub-app.stats"
    await client.close()


async def test_stale_frames_are_dropped(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    """Frames from before a stall must not be averaged into the first values after it."""
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    await client.host_id()
    client._realtime_at -= 120
    client._app_stats_at -= 120
    assert client.realtime_stats() is None
    assert client.app_stats() is None

    frame = load_fixture("reporting_realtime.json")
    server.junk = [
        event("reporting.realtime", {**frame, "cpu": {"cpu": {"usage": 50.0}}}),
        event("app.stats", [{"app_name": "plex", "cpu_usage": 9.0, "memory": 1}]),
    ]
    await client.host_id()
    assert client.realtime_stats().cpu_usage == 50.0
    assert client.app_stats() == {"plex": AppStats(9.0, 1)}
    await client.close()


async def test_realtime_notify_unsubscribed(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    server.junk = [
        {
            "jsonrpc": "2.0",
            "method": "notify_unsubscribed",
            "params": {"collection": "reporting.realtime", "error": {"error": 22, "reason": "netdata is down"}},
        },
        {"jsonrpc": "2.0", "method": "notify_unsubscribed", "params": {"collection": 5}},
    ]
    await client.host_id()
    assert set(client._subscriptions) == {"app.stats"}
    await client.close()


async def test_app_stats_denied(fake: tuple[FakeTrueNAS, str], session: aiohttp.ClientSession) -> None:
    server, url = fake
    server.denied_collections = {"app.stats"}
    client = TrueNASClient(session, "unused", VALID_KEY, ws_url=url)
    await client.connect()
    await client.host_id()
    assert client.realtime_stats() is not None
    assert client.app_stats() is None
    await client.close()


def test_stats_from_realtime() -> None:
    frame = load_fixture("reporting_realtime.json")
    stats = Stats.from_realtime(frame)
    assert stats.arc_hit_ratio == 95.0  # (140 + 50) / 200
    assert (stats.disk_read_rate, stats.disk_write_rate, stats.disk_busy) == (10485760, 5242880, 12.5)
    enp5s0, enp6s0 = stats.interfaces["enp5s0"], stats.interfaces["enp6s0"]
    assert (enp5s0.link_up, enp5s0.speed, enp5s0.rx_rate, enp5s0.tx_rate) == (True, 1000, 1250000, 250000)
    assert (enp6s0.link_up, enp6s0.speed) == (False, None)

    # An idle ARC (no reads) keeps the previous ratio.
    idle = {**frame, "zfs": {**frame["zfs"], "demand_accesses_per_second": 0}}
    assert Stats.from_realtime(idle, previous_arc_hit_ratio=97.5).arc_hit_ratio == 97.5

    # Malformed sections are ignored instead of failing the update.
    broken = Stats.from_realtime({"cpu": [], "memory": "x", "interfaces": ["eth0"], "disks": None, "zfs": 1})
    assert broken == Stats()
    odd = Stats.from_realtime({"interfaces": {"eth0": {"speed": float("inf"), "received_bytes_rate": float("nan")}}})
    assert (odd.interfaces["eth0"].speed, odd.interfaces["eth0"].rx_rate) == (None, None)


def test_system_info_hardware() -> None:
    info = SystemInfo.from_api(load_fixture("system_info.json"))
    assert (info.manufacturer, info.serial, info.ecc_memory) == ("ASUSTeK COMPUTER INC.", "MB-1234567890", True)
    assert info.build_time is not None

    # Generic boards leave DMI placeholders, which aren't worth showing.
    generic = SystemInfo.from_api(
        {
            "system_manufacturer": "To Be Filled By O.E.M.",
            "system_product": "Default string",
            "system_serial": " System Serial Number ",
            "system_product_version": "Rev X.0x",
            "ecc_memory": None,
        }
    )
    assert (generic.manufacturer, generic.model, generic.serial, generic.product_version, generic.ecc_memory) == (
        None,
        None,
        None,
        None,
        None,
    )


def test_update_check_available_states() -> None:
    assert UpdateInfo.from_check_available({"status": "UNAVAILABLE"}) == UpdateInfo(False)
    assert UpdateInfo.from_check_available({"status": "REBOOT_REQUIRED"}) == UpdateInfo(False, reboot_required=True)
    assert UpdateInfo.from_status({"code": "NORMAL", "status": {"new_version": None}}) == UpdateInfo(False)
