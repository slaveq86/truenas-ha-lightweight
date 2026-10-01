"""Diagnostics for TrueNAS Lightweight."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant

from .coordinator import TrueNASConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_HOST, "hostname", "title", "unique_id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TrueNASConfigEntry) -> dict[str, Any]:
    return async_redact_data(
        {
            "entry": entry.as_dict(),
            "data": asdict(entry.runtime_data.data),
        },
        TO_REDACT,
    )
