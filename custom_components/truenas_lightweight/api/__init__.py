"""TrueNAS JSON-RPC WebSocket API client."""

from .client import TrueNASClient
from .exceptions import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASPermissionError,
)
from .models import ALERT_LEVELS, TASK_STATES, Alert, App, Disk, Pool, Stats, SystemInfo, Task, TrueNASData

__all__ = [
    "ALERT_LEVELS",
    "TASK_STATES",
    "Alert",
    "App",
    "Disk",
    "Pool",
    "Stats",
    "SystemInfo",
    "Task",
    "TrueNASAuthError",
    "TrueNASClient",
    "TrueNASConnectionError",
    "TrueNASData",
    "TrueNASError",
    "TrueNASPermissionError",
]
