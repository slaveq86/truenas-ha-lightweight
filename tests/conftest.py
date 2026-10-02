"""Shared fixtures for TrueNAS Lightweight tests."""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_VERIFY_SSL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.truenas_lightweight.api import (
    Alert,
    App,
    AppStats,
    Disk,
    Pool,
    Service,
    Stats,
    SystemInfo,
    Task,
    UpdateInfo,
)
from custom_components.truenas_lightweight.const import DOMAIN

FIXTURES = Path(__file__).parent / "fixtures"
HOST_ID = "abc123hostid"

ENTRY_DATA = {
    CONF_HOST: "truenas.local",
    CONF_PORT: 443,
    CONF_VERIFY_SSL: False,
    CONF_API_KEY: "1-secretkey",
}


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow loading custom_components in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title="truenas", unique_id=HOST_ID, data=dict(ENTRY_DATA))


@pytest.fixture
def mock_client() -> Generator[AsyncMock]:
    """Patch TrueNASClient everywhere with fixture-backed responses."""
    with (
        patch("custom_components.truenas_lightweight.TrueNASClient", autospec=True) as cls,
        patch("custom_components.truenas_lightweight.config_flow.TrueNASClient", new=cls),
    ):
        client = cls.return_value
        client.host_id.return_value = HOST_ID
        client.system_info.return_value = SystemInfo.from_api(load_fixture("system_info.json"))
        client.alerts.return_value = [Alert.from_api(a) for a in load_fixture("alert_list.json")]
        client.pools.return_value = {p["name"]: Pool.from_api(p) for p in load_fixture("pool_query.json")}
        client.apps.return_value = {a["name"]: App.from_api(a) for a in load_fixture("app_query.json")}
        client.rsync_tasks.return_value = {
            str(t["id"]): Task.from_rsync(t) for t in load_fixture("rsynctask_query.json")
        }
        client.snapshot_tasks.return_value = {
            str(t["id"]): Task.from_snapshot(t) for t in load_fixture("snapshottask_query.json")
        }
        disks = (Disk.from_api(d) for d in load_fixture("disk_query.json"))
        client.disks.return_value = {d.key: d for d in disks}
        client.disk_temperatures.return_value = {
            name: t for name, t in load_fixture("disk_temperatures.json").items() if t is not None
        }
        client.realtime_stats.return_value = Stats.from_realtime(load_fixture("reporting_realtime.json"))
        client.app_stats.return_value = AppStats.from_samples([AppStats.sample(load_fixture("app_stats.json"))])
        client.services.return_value = {s["service"]: Service.from_api(s) for s in load_fixture("service_query.json")}
        client.update_info.return_value = UpdateInfo.from_check_available(load_fixture("update_check_available.json"))
        yield client
