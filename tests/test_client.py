"""Tests for the JSON-RPC WebSocket client against a fake TrueNAS server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.truenas_lightweight.api import (
    TrueNASAuthError,
    TrueNASClient,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASPermissionError,
)
from custom_components.truenas_lightweight.api.models import SystemInfo, Task

from .conftest import load_fixture

VALID_KEY = "1-good"


class FakeTrueNAS:
    """Minimal JSON-RPC 2.0 server mimicking /api/current."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.denied: set[str] = set()
        self.login_delay = 0.0
        self.login_started = asyncio.Event()
        self.responses: dict[str, Any] = {
            "system.info": load_fixture("system_info.json"),
            "system.host_id": "hostid",
            "alert.list": load_fixture("alert_list.json"),
            "pool.query": load_fixture("pool_query.json"),
            "app.query": load_fixture("app_query.json"),
            "rsynctask.query": load_fixture("rsynctask_query.json"),
            "pool.snapshottask.query": load_fixture("snapshottask_query.json"),
        }

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        authenticated = False
        async for msg in ws:
            req = msg.json()
            method, params, msg_id = req["method"], req["params"], req["id"]
            self.calls.append(method)
            if method == "auth.login_with_api_key":
                self.login_started.set()
                await asyncio.sleep(self.login_delay)
                authenticated = params == [VALID_KEY]
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": authenticated})
            elif not authenticated:
                await ws.send_json(_error(msg_id, "ENOTAUTHENTICATED", "Not authenticated"))
            elif method in self.denied:
                await ws.send_json(_error(msg_id, "EACCES", "Not authorized"))
            elif method == "core.subscribe":
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": "sub-1"})
                await ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "method": "collection_update",
                        "params": {
                            "msg": "added",
                            "collection": "reporting.realtime",
                            "fields": load_fixture("reporting_realtime.json"),
                        },
                    }
                )
            elif method in self.responses:
                await ws.send_json({"jsonrpc": "2.0", "id": msg_id, "result": self.responses[method]})
            else:
                await ws.send_json(
                    {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}
                )
        return ws


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

    info, pools, apps, alerts, rsync, snapshots = await asyncio.gather(
        client.system_info(),
        client.pools(),
        client.apps(),
        client.alerts(),
        client.rsync_tasks(),
        client.snapshot_tasks(),
    )

    assert info.hostname == "truenas"
    assert info.loadavg == (0.42, 0.37, 0.31)
    assert pools["backup"].healthy is False
    assert pools["tank"].used_pct == 81.0
    assert apps["plex"].upgrade_available is True
    assert [a.dismissed for a in alerts] == [False, False, True]
    assert {k: t.state for k, t in rsync.items()} == {"1": "SUCCESS", "2": "FAILED", "3": "PENDING"}
    assert {k: t.state for k, t in snapshots.items()} == {"1": "SUCCESS", "2": "FAILED"}
    # Concurrent calls must share a single login.
    assert server.calls.count("auth.login_with_api_key") == 1

    stats = client.realtime_stats()
    assert stats is not None
    assert stats.cpu_usage == 12.0
    assert stats.mem_used_pct == 75.0
    await client.close()
    assert not client.connected


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
    assert (never_run.state, never_run.last_run, never_run.failed) == ("PENDING", None, False)

    snapshot = Task.from_snapshot(load_fixture("snapshottask_query.json")[1])
    assert (snapshot.state, snapshot.error, snapshot.failed) == ("FAILED", "dataset is busy", True)
    assert snapshot.details["lifetime"] == "1 day"
    assert Task.from_snapshot(load_fixture("snapshottask_query.json")[0]).name == "tank/photos (recursive)"
