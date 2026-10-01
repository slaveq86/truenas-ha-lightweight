"""Constants for the TrueNAS Lightweight integration."""

from __future__ import annotations

import logging
from datetime import timedelta

DOMAIN = "truenas_lightweight"
LOGGER = logging.getLogger(__package__)

CONF_SCAN_INTERVAL = "scan_interval"

# Fired on the HA bus when an alert is raised in or cleared from TrueNAS.
EVENT_ALERT = f"{DOMAIN}_alert"

DEFAULT_PORT = 443
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 600

# disk.temperatures reads every disk's sensor (SMART/drivetemp); polling it each scan is wasteful and can keep HDDs
# from spinning down, so disks are refreshed on this slower cadence.
DISK_INTERVAL = timedelta(minutes=5)
