"""Tests for setup, entities and coordinator behaviour."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.truenas_lightweight.api import (
    App,
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASPermissionError,
)
from custom_components.truenas_lightweight.diagnostics import async_get_config_entry_diagnostics


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _tick(hass: HomeAssistant) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    # Coordinator refreshes run as background tasks.
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    expected = {
        "sensor.truenas_cpu_usage": "12.0",
        "sensor.truenas_memory_usage": "75.0",
        "sensor.truenas_load_1_min": "0.42",
        "sensor.truenas_version": "25.04.2",
        "sensor.truenas_last_boot": "2025-10-01T00:00:00+00:00",
        "sensor.truenas_active_alerts": "2",
        "sensor.truenas_highest_alert_level": "warning",
        "sensor.truenas_apps_running": "1",
        "sensor.truenas_pool_tank_status": "online",
        "sensor.truenas_pool_tank_usage": "81.0",
        "sensor.truenas_pool_backup_status": "degraded",
        "sensor.truenas_app_plex_state": "running",
        "sensor.truenas_app_nextcloud_state": "stopped",
        "binary_sensor.truenas_problem": STATE_ON,
        "binary_sensor.truenas_pool_tank_problem": STATE_OFF,
        "binary_sensor.truenas_pool_backup_problem": STATE_ON,
        "binary_sensor.truenas_app_plex_update": STATE_ON,
        "binary_sensor.truenas_app_nextcloud_update": STATE_OFF,
    }
    for entity_id, state in expected.items():
        assert (s := hass.states.get(entity_id)) is not None, entity_id
        assert s.state == state, entity_id

    alerts = hass.states.get("sensor.truenas_active_alerts").attributes["alerts"]
    assert alerts[0]["klass"] == "ZpoolCapacityWarning"

    memory = hass.states.get("sensor.truenas_memory_used")
    assert memory.attributes["unit_of_measurement"] == "GiB"
    assert float(memory.state) == 24.0

    # Disabled by default.
    assert er.async_get(hass).async_get("sensor.truenas_cpu_temperature").disabled_by is not None


async def test_new_and_removed_apps(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    mock_client.apps.return_value = {"jellyfin": App("jellyfin", "DEPLOYING", "1.0", False)}
    await _tick(hass)

    assert hass.states.get("sensor.truenas_app_jellyfin_state").state == "deploying"
    assert hass.states.get("sensor.truenas_app_plex_state").state == STATE_UNAVAILABLE


async def test_missing_realtime_stats(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.realtime_stats.return_value = None
    await _setup(hass, mock_config_entry)
    assert hass.states.get("sensor.truenas_cpu_usage").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.truenas_version").state == "25.04.2"


async def test_permission_denied_is_partial(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.apps.side_effect = TrueNASPermissionError("EACCES")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.truenas_apps_running").state == "0"
    assert hass.states.get("sensor.truenas_app_plex_state") is None


async def test_setup_retry(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    mock_client.system_info.side_effect = TrueNASConnectionError("down")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited()


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    mock_client.system_info.side_effect = TrueNASAuthError("revoked")
    await _tick(hass)

    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_unload(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_config_entry)
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited()


async def test_diagnostics_redacts(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diag["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["data"]["system"]["hostname"] == "**REDACTED**"
    assert "1-secretkey" not in str(diag)
