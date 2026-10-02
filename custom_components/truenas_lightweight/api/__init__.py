"""TrueNAS JSON-RPC WebSocket API client."""

from .client import TrueNASClient
from .exceptions import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASMethodNotFoundError,
    TrueNASPermissionError,
)
from .models import (
    ALERT_LEVELS,
    TASK_STATES,
    Alert,
    App,
    AppStats,
    Disk,
    Interface,
    Pool,
    RealtimeSample,
    Service,
    Stats,
    SystemInfo,
    Task,
    TrueNASData,
    UpdateInfo,
)

__all__ = [
    "ALERT_LEVELS",
    "TASK_STATES",
    "Alert",
    "App",
    "AppStats",
    "Disk",
    "Interface",
    "Pool",
    "RealtimeSample",
    "Service",
    "Stats",
    "SystemInfo",
    "Task",
    "TrueNASAuthError",
    "TrueNASClient",
    "TrueNASConnectionError",
    "TrueNASData",
    "TrueNASError",
    "TrueNASMethodNotFoundError",
    "TrueNASPermissionError",
    "UpdateInfo",
]
