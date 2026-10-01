"""Diagnostics for TrueNAS Lightweight."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import TrueNASConfigEntry

# Pool/app/task names, datasets and remote targets identify the setup; TrueNAS also writes hostnames, IPs, paths
# and disk serials into free-text task errors and alert messages.
TO_REDACT = {
    CONF_API_KEY,
    CONF_HOST,
    "hostname",
    "title",
    "unique_id",
    "name",
    "path",
    "dataset",
    "remotehost",
    "remote",
    "error",
    "message",
}

# async_redact_data only redacts values, and these are keyed by pool/app name (tasks by id).
KEYED_BY_NAME = ("pools", "apps", "rsync_tasks", "snapshot_tasks")


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TrueNASConfigEntry) -> dict[str, Any]:
    data = asdict(entry.runtime_data.data)
    for key in KEYED_BY_NAME:
        data[key] = list(data[key].values())
    return async_redact_data({"entry": entry.as_dict(), "data": data}, TO_REDACT)
