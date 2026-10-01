"""Tests for setup, entities and coordinator behaviour."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.truenas_lightweight.api import (
    Alert,
    App,
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASPermissionError,
)
from custom_components.truenas_lightweight.const import EVENT_ALERT
from custom_components.truenas_lightweight.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST_ID


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
        "sensor.truenas_rsync_photos_to_backup_status": "success",
        "sensor.truenas_rsync_photos_to_backup_last_run": "2025-10-01T06:36:40+00:00",
        "binary_sensor.truenas_rsync_photos_to_backup_problem": STATE_OFF,
        "sensor.truenas_rsync_mnt_tank_docs_status": "failed",
        "binary_sensor.truenas_rsync_mnt_tank_docs_problem": STATE_ON,
        "sensor.truenas_rsync_never_run_status": "pending",
        "sensor.truenas_rsync_never_run_last_run": "unknown",
        "sensor.truenas_snapshot_tank_photos_recursive_status": "success",
        "binary_sensor.truenas_snapshot_tank_photos_recursive_problem": STATE_OFF,
        "sensor.truenas_snapshot_tank_vms_status": "failed",
        "binary_sensor.truenas_snapshot_tank_vms_problem": STATE_ON,
    }
    for entity_id, state in expected.items():
        assert (s := hass.states.get(entity_id)) is not None, entity_id
        assert s.state == state, entity_id

    alerts = hass.states.get("sensor.truenas_active_alerts").attributes["alerts"]
    assert alerts[0]["klass"] == "ZpoolCapacityWarning"

    memory = hass.states.get("sensor.truenas_memory_used")
    assert memory.attributes["unit_of_measurement"] == "GiB"
    assert float(memory.state) == 24.0

    docs = hass.states.get("sensor.truenas_rsync_mnt_tank_docs_status").attributes
    assert docs["direction"] == "pull"
    assert docs["remote"] == "docs"
    assert docs["error"].startswith("rsync command returned 255")
    assert hass.states.get("binary_sensor.truenas_snapshot_tank_vms_problem").attributes["error"] == "dataset is busy"
    assert (
        er.async_get(hass).async_get("sensor.truenas_rsync_never_run_status").unique_id == f"{HOST_ID}_rsync_3_status"
    )

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


async def test_new_and_removed_tasks(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    tasks = mock_client.rsync_tasks.return_value
    running = replace(tasks["1"], state="RUNNING")
    mock_client.rsync_tasks.return_value = {"1": running, "9": replace(tasks["3"], id=9, name="New task")}
    await _tick(hass)

    assert hass.states.get("sensor.truenas_rsync_photos_to_backup_status").state == "running"
    assert hass.states.get("sensor.truenas_rsync_new_task_status").state == "pending"
    assert hass.states.get("sensor.truenas_rsync_mnt_tank_docs_status").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.truenas_rsync_mnt_tank_docs_problem").state == STATE_UNAVAILABLE


async def test_alert_events(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    events = async_capture_events(hass, EVENT_ALERT)
    await _setup(hass, mock_config_entry)
    # The first refresh only records the current alerts.
    assert events == []

    alerts = mock_client.alerts.return_value
    new = Alert("a9", "SMART", "CRITICAL", "Disk sda failing.", False, None)
    # a1 cleared, a9 raised, a2 unchanged, a3 still dismissed.
    mock_client.alerts.return_value = [alerts[1], alerts[2], new]
    await _tick(hass)

    assert [(e.data["action"], e.data["uuid"]) for e in events] == [("raised", "a9"), ("cleared", "a1")]
    raised = events[0].data
    assert raised["level"] == "CRITICAL"
    assert raised["klass"] == "SMART"
    assert raised["message"] == "Disk sda failing."
    assert raised["hostname"] == "truenas"
    assert raised["config_entry_id"] == mock_config_entry.entry_id
    assert events[1].data["datetime"] == "2025-10-01T06:26:40+00:00"

    await _tick(hass)
    assert len(events) == 2


async def test_alert_permission_denied_fires_nothing(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_ALERT)
    await _setup(hass, mock_config_entry)
    mock_client.alerts.side_effect = TrueNASPermissionError("EACCES")
    await _tick(hass)

    assert events == []
    assert hass.states.get("sensor.truenas_active_alerts").state == "0"


async def test_boot_time_stable_until_reboot(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    original = mock_client.system_info.return_value
    state = hass.states.get("sensor.truenas_last_boot").state

    # Poll jitter (computed from uptime) must not move the timestamp.
    mock_client.system_info.return_value = replace(original, boot_time=original.boot_time + timedelta(seconds=61))
    await _tick(hass)
    assert hass.states.get("sensor.truenas_last_boot").state == state

    # A real reboot does.
    mock_client.system_info.return_value = replace(original, boot_time=original.boot_time + timedelta(days=3))
    await _tick(hass)
    assert hass.states.get("sensor.truenas_last_boot").state == "2025-10-04T00:00:00+00:00"


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
    assert diag["data"]["rsync_tasks"]["1"]["details"]["remotehost"] == "**REDACTED**"
    assert "backup.lan" not in str(diag)
    assert "1-secretkey" not in str(diag)
