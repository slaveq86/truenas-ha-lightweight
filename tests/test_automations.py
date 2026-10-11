"""The example automations in automations/ fire on the right state changes."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import yaml
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

AUTOMATIONS = Path(__file__).parent.parent / "automations"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, automation: str) -> list[ServiceCall]:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    config: dict[str, Any] = yaml.safe_load((AUTOMATIONS / automation).read_text())
    # The notify target is a placeholder device; send to a mock service instead.
    for action in config["actions"]:
        if action.get("action") == "notify.send_message":
            action["action"] = "test.notify"
            del action["target"]
    calls = async_mock_service(hass, "test", "notify")
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    return calls


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta) -> None:
    freezer.tick(delta)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_data_protection_failed(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    calls = await _setup(hass, mock_config_entry, "data_protection_failed.yaml")
    tasks = mock_client.rsync_tasks.return_value
    ok = tasks["1"]

    # Task 2 was already failing at startup: no notification.
    await _advance(hass, freezer, timedelta(seconds=31))
    assert calls == []

    mock_client.rsync_tasks.return_value = {**tasks, "1": replace(ok, state="FAILED", error="rsync exited with 23")}
    await _advance(hass, freezer, timedelta(seconds=31))
    assert [c.data for c in calls] == [
        {"title": "TrueNAS data protection", "message": "Rsync Photos to backup failed: rsync exited with 23"}
    ]

    # Still failing: no repeat. Recovers and fails again: notifies again.
    await _advance(hass, freezer, timedelta(seconds=31))
    mock_client.rsync_tasks.return_value = tasks
    await _advance(hass, freezer, timedelta(seconds=31))
    mock_client.rsync_tasks.return_value = {**tasks, "1": replace(ok, state="ABORTED", error=None)}
    await _advance(hass, freezer, timedelta(seconds=31))
    assert len(calls) == 2
    assert calls[1].data["message"] == "Rsync Photos to backup failed"

    # Other problem sensors (pools, host) are ignored.
    hass.states.async_set("binary_sensor.truenas_problem", "on", {"device_class": "problem"})
    await hass.async_block_till_done()
    assert len(calls) == 2


async def test_disk_temperature_high(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    calls = await _setup(hass, mock_config_entry, "disk_temperature_high.yaml")
    temps = mock_client.disk_temperatures.return_value

    async def read(**changed: int) -> None:
        mock_client.disk_temperatures.return_value = {**temps, **changed}
        await _advance(hass, freezer, timedelta(minutes=5))

    await read(sdc=60)
    assert calls == []

    await read(sdc=61.4)
    assert len(calls) == 1
    assert calls[0].data["title"] == "TrueNAS disk temperature"
    assert calls[0].data["message"].startswith("Disk sdc (")
    assert calls[0].data["message"].endswith("is at 61 °C (above 60 °C).")

    # Staying hot doesn't repeat; cooling down and heating up again does.
    await read(sdc=65)
    await read(sdc=58)
    await read(sdc=62)
    assert len(calls) == 2

    # CPU temperature isn't a disk.
    hass.states.async_set("sensor.truenas_cpu_temperature", "50", {"device_class": "temperature"})
    hass.states.async_set("sensor.truenas_cpu_temperature", "90", {"device_class": "temperature"})
    await hass.async_block_till_done()
    assert len(calls) == 2
