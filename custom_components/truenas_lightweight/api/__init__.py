"""TrueNAS JSON-RPC WebSocket API client."""

from .client import TrueNASClient
from .exceptions import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASError,
    TrueNASPermissionError,
)
from .models import ALERT_LEVELS, Alert, App, Pool, Stats, SystemInfo, TrueNASData

__all__ = [
    "ALERT_LEVELS",
    "Alert",
    "App",
    "Pool",
    "Stats",
    "SystemInfo",
    "TrueNASAuthError",
    "TrueNASClient",
    "TrueNASConnectionError",
    "TrueNASData",
    "TrueNASError",
    "TrueNASPermissionError",
]
