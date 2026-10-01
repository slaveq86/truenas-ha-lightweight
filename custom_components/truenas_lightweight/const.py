"""Constants for the TrueNAS Lightweight integration."""

from __future__ import annotations

import logging

DOMAIN = "truenas_lightweight"
LOGGER = logging.getLogger(__package__)

CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 443
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600
