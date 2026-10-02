"""Tests for setup, entities and coordinator behaviour."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.truenas_lightweight import async_remove_config_entry_device
from custom_components.truenas_lightweight.api import (
    Alert,
    App,
    Interface,
    Service,
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASPermissionError,
    UpdateInfo,
)
from custom_components.truenas_lightweight.const import DOMAIN, EVENT_ALERT
from custom_components.truenas_lightweight.diagnostics import async_get_config_entry_diagnostics

from .conftest import HOST_ID


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    # The update check runs as a background task.
    await hass.async_block_till_done(wait_background_tasks=True)


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
        "sensor.truenas_apps_plex_state": "running",
        "sensor.truenas_apps_nextcloud_state": "stopped",
        "binary_sensor.truenas_problem": STATE_ON,
        "binary_sensor.truenas_pool_tank_problem": STATE_OFF,
        "binary_sensor.truenas_pool_backup_problem": STATE_ON,
        "binary_sensor.truenas_apps_plex_update": STATE_ON,
        "binary_sensor.truenas_apps_nextcloud_update": STATE_OFF,
        "sensor.truenas_data_protection_rsync_photos_to_backup_status": "success",
        "sensor.truenas_data_protection_rsync_photos_to_backup_last_run": "2025-10-01T06:36:40+00:00",
        "binary_sensor.truenas_data_protection_rsync_photos_to_backup_problem": STATE_OFF,
        "sensor.truenas_data_protection_rsync_mnt_tank_docs_status": "failed",
        "binary_sensor.truenas_data_protection_rsync_mnt_tank_docs_problem": STATE_ON,
        "sensor.truenas_data_protection_rsync_never_run_status": "pending",
        "sensor.truenas_data_protection_rsync_never_run_last_run": "unknown",
        "sensor.truenas_data_protection_snapshot_tank_photos_recursive_2_weeks_status": "success",
        "binary_sensor.truenas_data_protection_snapshot_tank_photos_recursive_2_weeks_problem": STATE_OFF,
        "sensor.truenas_data_protection_snapshot_tank_photos_recursive_48_hours_status": "hold",
        "binary_sensor.truenas_data_protection_snapshot_tank_photos_recursive_48_hours_problem": STATE_ON,
        "sensor.truenas_data_protection_snapshot_tank_vms_1_day_status": "failed",
        "binary_sensor.truenas_data_protection_snapshot_tank_vms_1_day_problem": STATE_ON,
        "sensor.truenas_pool_tank_disk_sda_temperature": "34",
        "sensor.truenas_pool_tank_disk_sdb_temperature": "36",
        "sensor.truenas_pool_backup_disk_sdc_temperature": "52",
        "sensor.truenas_disk_nvme0n1_temperature": "45",
        "sensor.truenas_disk_sdd_temperature": "unknown",
    }
    for entity_id, state in expected.items():
        assert (s := hass.states.get(entity_id)) is not None, entity_id
        assert s.state == state, entity_id

    alerts = hass.states.get("sensor.truenas_active_alerts").attributes["alerts"]
    assert alerts[0]["klass"] == "ZpoolCapacityWarning"

    memory = hass.states.get("sensor.truenas_memory_used")
    assert memory.attributes["unit_of_measurement"] == "GiB"
    assert float(memory.state) == 24.0

    docs = hass.states.get("sensor.truenas_data_protection_rsync_mnt_tank_docs_status").attributes
    assert docs["direction"] == "pull"
    assert docs["remote"] == "docs"
    assert docs["error"].startswith("rsync command returned 255")
    assert hass.states.get("binary_sensor.truenas_data_protection_snapshot_tank_vms_1_day_problem").attributes[
        "error"
    ] == ("dataset is busy")
    assert (
        er.async_get(hass).async_get("sensor.truenas_data_protection_rsync_never_run_status").unique_id
        == f"{HOST_ID}_rsync_3_status"
    )

    sda = hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").attributes
    assert sda["unit_of_measurement"] == "°C"
    assert sda["serial"] == "WD-WCC7K1ABCDEF"
    assert sda["model"] == "WDC WD40EFRX-68N32N0"
    assert sda["type"] == "HDD"
    assert sda["pool"] == "tank"
    # Keyed by serial, not by the kernel name that can change between boots.
    assert (
        er.async_get(hass).async_get("sensor.truenas_pool_tank_disk_sda_temperature").unique_id
        == f"{HOST_ID}_disk_WD-WCC7K1ABCDEF_temperature"
    )

    # Disabled by default.
    assert er.async_get(hass).async_get("sensor.truenas_cpu_temperature").disabled_by is not None


async def test_new_entities(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_config_entry)
    expected = {
        "sensor.truenas_zfs_arc_hit_ratio": "95.0",
        "sensor.truenas_disk_busy": "12.5",
        "binary_sensor.truenas_ecc_memory": STATE_ON,
        "sensor.truenas_apps_plex_cpu_usage": "3.5",
        "sensor.truenas_apps_nextcloud_cpu_usage": "0.0",
        "binary_sensor.truenas_network_enp5s0_link": STATE_ON,
        "binary_sensor.truenas_network_enp6s0_link": STATE_OFF,
        "binary_sensor.truenas_services_smb": STATE_ON,
        "binary_sensor.truenas_services_ssh": STATE_ON,
        "binary_sensor.truenas_services_nfs": STATE_OFF,
        "update.truenas_update": STATE_ON,
    }
    for entity_id, state in expected.items():
        assert (s := hass.states.get(entity_id)) is not None, entity_id
        assert s.state == state, entity_id

    def value(entity_id: str) -> tuple[float, str]:
        state = hass.states.get(entity_id)
        return float(state.state), state.attributes["unit_of_measurement"]

    assert value("sensor.truenas_disk_read_rate") == (10.48576, "MB/s")
    assert value("sensor.truenas_network_enp5s0_download") == (10.0, "Mbit/s")
    assert value("sensor.truenas_network_enp5s0_upload") == (2.0, "Mbit/s")
    assert value("sensor.truenas_apps_plex_memory") == (512.0, "MiB")
    assert hass.states.get("binary_sensor.truenas_network_enp5s0_link").attributes["speed_mbps"] == 1000
    assert hass.states.get("binary_sensor.truenas_services_nfs").attributes["enabled"] is True
    assert "version" not in hass.states.get("sensor.truenas_apps_plex_cpu_usage").attributes
    assert hass.states.get("sensor.truenas_version").attributes["build_time"] == "2025-07-28T10:53:20+00:00"

    update = hass.states.get("update.truenas_update").attributes
    assert (update["installed_version"], update["latest_version"]) == ("25.04.2", "25.04.3")
    assert update["release_url"].endswith("#25043-changelog")
    # Read-only: nothing can be installed from Home Assistant.
    assert update["supported_features"] == 0

    entities = er.async_get(hass)
    assert entities.async_get("binary_sensor.truenas_services_smb").unique_id == f"{HOST_ID}_service_cifs"
    assert (
        entities.async_get("sensor.truenas_network_enp5s0_download").unique_id == f"{HOST_ID}_interface_enp5s0_download"
    )
    assert entities.async_get("sensor.truenas_apps_plex_memory").unique_id == f"{HOST_ID}_app_plex_memory"
    # Services that don't start on boot in TrueNAS are disabled by default.
    assert entities.async_get("binary_sensor.truenas_services_ftp").disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entities.async_get("binary_sensor.truenas_services_smb").disabled_by is None


async def test_interfaces_follow_realtime_stats(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    stats = mock_client.realtime_stats.return_value

    mock_client.realtime_stats.return_value = None
    await _tick(hass)
    assert hass.states.get("sensor.truenas_network_enp5s0_download").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.truenas_network_enp5s0_link").state == STATE_UNAVAILABLE

    mock_client.realtime_stats.return_value = replace(
        stats, interfaces={"br0": Interface("br0", True, None, 125000, 0)}
    )
    await _tick(hass)
    assert hass.states.get("sensor.truenas_network_br0_download").state == "1.0"
    assert hass.states.get("sensor.truenas_network_enp5s0_download").state == STATE_UNAVAILABLE
    # The ARC hit ratio of the previous read is handed back for idle windows.
    mock_client.realtime_stats.assert_called_with(95.0)


async def test_services_change(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_config_entry)
    mock_client.services.return_value = {"nfs": Service("nfs", True, "RUNNING")}
    await _tick(hass)
    assert hass.states.get("binary_sensor.truenas_services_nfs").state == STATE_ON
    assert hass.states.get("binary_sensor.truenas_services_smb").state == STATE_UNAVAILABLE

    mock_client.services.side_effect = TrueNASPermissionError("EACCES")
    await _tick(hass)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.truenas_services_nfs").state == STATE_UNAVAILABLE


async def test_update_checked_slowly(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, mock_config_entry)

    async def advance(delta: timedelta) -> None:
        freezer.tick(delta)
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    await advance(timedelta(seconds=31))
    assert mock_client.update_info.await_count == 1

    # A failed check keeps the last result and is retried after UPDATE_RETRY, not on every scan.
    mock_client.update_info.side_effect = TrueNASConnectionError("Timeout calling update.check_available")
    await advance(timedelta(hours=6))
    assert mock_client.update_info.await_count == 2
    assert hass.states.get("update.truenas_update").state == STATE_ON
    await advance(timedelta(seconds=31))
    assert mock_client.update_info.await_count == 2
    await advance(timedelta(minutes=30))
    assert mock_client.update_info.await_count == 3

    # Installing the update (new version) triggers a check right away.
    mock_client.update_info.side_effect = None
    mock_client.update_info.return_value = UpdateInfo(False)
    mock_client.system_info.return_value = replace(mock_client.system_info.return_value, version="25.04.3")
    await advance(timedelta(seconds=31))
    assert mock_client.update_info.await_count == 4
    state = hass.states.get("update.truenas_update")
    assert (state.state, state.attributes["latest_version"]) == (STATE_OFF, "25.04.3")


async def test_slow_update_check_does_not_block(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """On 25.04 TrueNAS asks the iX update server; a slow answer must not hold up setup or refreshes."""
    release = asyncio.Event()
    update = mock_client.update_info.return_value

    async def slow_update() -> UpdateInfo:
        await release.wait()
        return update

    mock_client.update_info.side_effect = slow_update
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("update.truenas_update").state == STATE_UNAVAILABLE

    # Refreshes go on, without starting a second check while the first is still running. (Not `_tick`: waiting for
    # background tasks would wait for the held check.)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert mock_client.system_info.await_count == 2
    assert mock_client.update_info.await_count == 1

    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("update.truenas_update").state == STATE_ON


async def test_update_denied(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    mock_client.update_info.side_effect = TrueNASPermissionError("EACCES")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("update.truenas_update").state == STATE_UNAVAILABLE


async def test_update_reboot_required(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.update_info.return_value = UpdateInfo(False, reboot_required=True)
    await _setup(hass, mock_config_entry)
    state = hass.states.get("update.truenas_update")
    assert state.state == STATE_OFF
    assert "reboot" in state.attributes["release_summary"]


async def test_app_stats_resubscribe(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    mock_client.app_stats.return_value = None
    await _setup(hass, mock_config_entry)
    assert hass.states.get("sensor.truenas_apps_plex_cpu_usage").state == "unknown"

    async def advance(delta: timedelta) -> None:
        freezer.tick(delta)
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    # The subscription made on connect gets time to deliver before it is renewed, then only every 5 minutes.
    await advance(timedelta(seconds=31))
    mock_client.resubscribe.assert_not_awaited()
    await advance(timedelta(minutes=5))
    mock_client.resubscribe.assert_awaited_once_with("app.stats")
    await advance(timedelta(seconds=31))
    assert mock_client.resubscribe.await_count == 1

    # No running app: nothing to get stats for.
    mock_client.apps.return_value = {"nextcloud": mock_client.apps.return_value["nextcloud"]}
    await advance(timedelta(minutes=5))
    assert mock_client.resubscribe.await_count == 1

    # A failing resubscribe (e.g. connection lost) doesn't fail the refresh.
    mock_client.apps.return_value = {"plex": App("plex", "RUNNING", "1.0", False)}
    mock_client.resubscribe.side_effect = TrueNASConnectionError("down")
    await advance(timedelta(minutes=5))
    assert mock_client.resubscribe.await_count == 2
    assert hass.states.get("sensor.truenas_apps_plex_state").state == "running"


async def test_realtime_resubscribe(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A realtime feed that stops (e.g. the server dropped the subscription) is subscribed again."""
    await _setup(hass, mock_config_entry)
    mock_client.realtime_stats.return_value = None
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    mock_client.resubscribe.assert_awaited_once_with("reporting.realtime")


async def test_child_devices(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    await _setup(hass, mock_config_entry)
    devices = dr.async_get(hass)
    host = devices.async_get_device({(DOMAIN, HOST_ID)})
    assert host.name == "truenas"
    # Hardware details from DMI.
    # "Rev X.0x" is ASUS's placeholder revision and is left out.
    assert (host.manufacturer, host.model, host.hw_version, host.serial_number) == (
        "ASUSTeK COMPUTER INC.",
        "B550M",
        None,
        "MB-1234567890",
    )

    children = {d.name: d for d in dr.async_entries_for_config_entry(devices, mock_config_entry.entry_id) if d != host}
    assert set(children) == {
        "truenas Pool tank",
        "truenas Pool backup",
        "truenas Apps",
        "truenas Data protection",
        "truenas Network",
        "truenas Services",
    }
    assert all(d.via_device_id == host.id for d in children.values())

    entities = er.async_get(hass)
    for entity_id, device in {
        "sensor.truenas_cpu_usage": "truenas",
        "sensor.truenas_pool_tank_usage": "truenas Pool tank",
        "binary_sensor.truenas_pool_backup_problem": "truenas Pool backup",
        "sensor.truenas_apps_running": "truenas Apps",
        "sensor.truenas_apps_plex_state": "truenas Apps",
        "binary_sensor.truenas_data_protection_snapshot_tank_vms_1_day_problem": "truenas Data protection",
        "sensor.truenas_pool_backup_disk_sdc_temperature": "truenas Pool backup",
        # The boot pool has no pool device; boot and unassigned disks sit on the host.
        "sensor.truenas_disk_nvme0n1_temperature": "truenas",
        "sensor.truenas_disk_sdd_temperature": "truenas",
        "sensor.truenas_disk_read_rate": "truenas",
        "update.truenas_update": "truenas",
        "sensor.truenas_apps_plex_cpu_usage": "truenas Apps",
        "sensor.truenas_network_enp5s0_download": "truenas Network",
        "binary_sensor.truenas_network_enp5s0_link": "truenas Network",
        "binary_sensor.truenas_services_smb": "truenas Services",
    }.items():
        assert devices.async_get(entities.async_get(entity_id).device_id).name == device, entity_id


async def test_upgrade_keeps_entity_ids(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    entities = er.async_get(hass)
    old = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{HOST_ID}_app_plex_state",
        config_entry=mock_config_entry,
        suggested_object_id="truenas_app_plex_state",
    )
    await _setup(hass, mock_config_entry)

    assert hass.states.get(old.entity_id).state == "running"
    device = dr.async_get(hass).async_get(entities.async_get(old.entity_id).device_id)
    assert device.name == "truenas Apps"


async def test_remove_stale_pool_device(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    devices = dr.async_get(hass)

    async def removable(ident: str) -> bool:
        return await async_remove_config_entry_device(
            hass, mock_config_entry, devices.async_get_device({(DOMAIN, ident)})
        )

    assert not await removable(f"{HOST_ID}_pool_backup")
    assert not await removable(HOST_ID)
    assert not await removable(f"{HOST_ID}_apps")

    mock_client.pools.return_value = {"tank": mock_client.pools.return_value["tank"]}
    await _tick(hass)
    assert await removable(f"{HOST_ID}_pool_backup")
    assert not await removable(f"{HOST_ID}_pool_tank")


async def test_new_and_removed_apps(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    mock_client.apps.return_value = {"jellyfin": App("jellyfin", "DEPLOYING", "1.0", False)}
    await _tick(hass)

    assert hass.states.get("sensor.truenas_apps_jellyfin_state").state == "deploying"
    assert hass.states.get("sensor.truenas_apps_plex_state").state == STATE_UNAVAILABLE


async def test_new_and_removed_tasks(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    await _setup(hass, mock_config_entry)
    tasks = mock_client.rsync_tasks.return_value
    running = replace(tasks["1"], state="RUNNING")
    mock_client.rsync_tasks.return_value = {"1": running, "9": replace(tasks["3"], id=9, name="New task")}
    await _tick(hass)

    assert hass.states.get("sensor.truenas_data_protection_rsync_photos_to_backup_status").state == "running"
    assert hass.states.get("sensor.truenas_data_protection_rsync_new_task_status").state == "pending"
    assert hass.states.get("sensor.truenas_data_protection_rsync_mnt_tank_docs_status").state == STATE_UNAVAILABLE
    assert (
        hass.states.get("binary_sensor.truenas_data_protection_rsync_mnt_tank_docs_problem").state == STATE_UNAVAILABLE
    )


async def test_disks_refresh_slowly(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _setup(hass, mock_config_entry)
    mock_client.disk_temperatures.return_value = {"sda": 40}

    async def advance(delta: timedelta) -> None:
        freezer.tick(delta)
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    # Regular scans reuse the cached disks.
    await advance(timedelta(seconds=31))
    assert mock_client.system_info.await_count == 2
    assert mock_client.disk_temperatures.await_count == 1
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").state == "34"

    await advance(timedelta(minutes=5))
    assert mock_client.disk_temperatures.await_count == 2
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").state == "40"
    assert hass.states.get("sensor.truenas_pool_tank_disk_sdb_temperature").state == "unknown"

    # A removed disk goes unavailable.
    mock_client.disks.return_value = {k: d for k, d in mock_client.disks.return_value.items() if d.name != "sdb"}
    await advance(timedelta(minutes=5))
    assert hass.states.get("sensor.truenas_pool_tank_disk_sdb_temperature").state == STATE_UNAVAILABLE


async def test_disk_permission_denied(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.disk_temperatures.side_effect = TrueNASPermissionError("EACCES")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    # Disks are still known, just without a temperature.
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").state == "unknown"


async def test_disk_errors_degrade_and_back_off(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    # Slow SMART reads can time out; that must not take every other entity down with it.
    mock_client.disk_temperatures.side_effect = TrueNASConnectionError("Timeout calling disk.temperatures")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.truenas_cpu_usage").state == "12.0"
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").state == "unknown"

    # Not retried on every scan.
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.disk_temperatures.await_count == 1

    # A failing disk.query keeps the known disks; temperatures recover.
    mock_client.disk_temperatures.side_effect = None
    mock_client.disks.side_effect = TrueNASConnectionError("Timeout calling disk.query")
    freezer.tick(timedelta(minutes=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_client.disk_temperatures.await_count == 2
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature").state == "34"


async def test_disk_auth_error_starts_reauth(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.disk_temperatures.side_effect = TrueNASAuthError("revoked")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_disk_query_denied(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_client.disks.side_effect = TrueNASPermissionError("EACCES")
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.truenas_pool_tank_disk_sda_temperature") is None


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


async def test_alert_permission_restored_does_not_replay(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    events = async_capture_events(hass, EVENT_ALERT)
    await _setup(hass, mock_config_entry)
    alerts = mock_client.alerts.return_value

    mock_client.alerts.side_effect = TrueNASPermissionError("EACCES")
    await _tick(hass)
    # Access is back: the long-standing alerts only re-seed the baseline.
    mock_client.alerts.side_effect = None
    await _tick(hass)
    assert events == []

    mock_client.alerts.return_value = [*alerts, Alert("a9", "SMART", "CRITICAL", "Disk sda failing.", False, None)]
    await _tick(hass)
    assert [(e.data["action"], e.data["uuid"]) for e in events] == [("raised", "a9")]


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
    assert hass.states.get("sensor.truenas_apps_plex_state") is None


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
    rsync = diag["data"]["rsync_tasks"][0]
    assert rsync["details"]["remotehost"] == "**REDACTED**"
    # Non-identifying fields stay readable.
    assert rsync["state"] == "SUCCESS"
    assert diag["data"]["pools"][0]["status"] == "ONLINE"
    assert diag["data"]["disks"][0]["temperature"] == 34
    assert diag["data"]["system"]["serial"] == "**REDACTED**"
    assert diag["data"]["services"][0]["state"] == "STOPPED"
    assert diag["data"]["update"]["version"] == "25.04.3"
    # Pool names (also dict keys), datasets, task names derived from paths, remotes and free text are all gone.
    text = str(diag)
    for secret in (
        "1-secretkey",
        "backup.lan",
        "nas2.lan",
        "/srv/photos",
        "/mnt/tank",
        "tank",
        "photos",
        "plex",
        "WD-WCC7K1",
        "MB-1234567890",
    ):
        assert secret not in text, secret
