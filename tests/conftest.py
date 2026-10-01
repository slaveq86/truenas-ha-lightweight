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

from custom_components.truenas_lightweight.api import Alert, App, Pool, Stats, SystemInfo
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
        client.realtime_stats.return_value = Stats.from_realtime(load_fixture("reporting_realtime.json"))
        yield client
