"""Exceptions raised by the TrueNAS API client."""

from __future__ import annotations


class TrueNASError(Exception):
    """Base error for the TrueNAS API."""


class TrueNASConnectionError(TrueNASError):
    """Connection to TrueNAS failed, dropped or timed out."""


class TrueNASAuthError(TrueNASError):
    """API key was rejected or the session is not authenticated."""


class TrueNASPermissionError(TrueNASError):
    """API key's user lacks the role required for a method (EACCES)."""


class TrueNASMethodNotFoundError(TrueNASError):
    """The method doesn't exist on this TrueNAS release (JSON-RPC -32601)."""
