"""Tests for the config and options flows."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.truenas_lightweight.api import (
    TrueNASAuthError,
    TrueNASConnectionError,
    TrueNASPermissionError,
)
from custom_components.truenas_lightweight.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import ENTRY_DATA, HOST_ID


@pytest.fixture(autouse=True)
def no_setup() -> None:
    with patch("custom_components.truenas_lightweight.async_setup_entry", return_value=True):
        yield


async def test_user_flow(hass: HomeAssistant, mock_client: AsyncMock) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**ENTRY_DATA, CONF_HOST: "https://truenas.local/ui/dashboard"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "truenas"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == HOST_ID
    mock_client.close.assert_awaited()


@pytest.mark.parametrize(
    ("host_input", "host", "port"),
    [
        ("https://truenas.local:8443/ui", "truenas.local", 8443),
        ("truenas.local:8443", "truenas.local", 8443),
        ("[fd00::10]:8443", "fd00::10", 8443),
        ("fd00::10", "fd00::10", 443),
        (" 192.168.1.5 ", "192.168.1.5", 443),
    ],
)
async def test_user_flow_host_parsing(
    hass: HomeAssistant, mock_client: AsyncMock, host_input: str, host: str, port: int
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ENTRY_DATA, CONF_HOST: host_input})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == host
    assert result["data"][CONF_PORT] == port


@pytest.mark.parametrize("host_input", ["https://", "nas.local:notaport", "nas.local:99999"])
async def test_user_flow_invalid_host(hass: HomeAssistant, mock_client: AsyncMock, host_input: str) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ENTRY_DATA, CONF_HOST: host_input})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    mock_client.host_id.assert_not_called()


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (TrueNASAuthError, "invalid_auth"),
        (TrueNASPermissionError, "insufficient_permissions"),
        (TrueNASConnectionError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, mock_client: AsyncMock, error: type[Exception], key: str) -> None:
    mock_client.host_id.side_effect = error("boom")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    # Recovers once the problem is fixed.
    mock_client.host_id.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "2-newkey"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_KEY] == "2-newkey"


async def test_reauth_wrong_device(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    mock_config_entry.add_to_hass(hass)
    mock_client.host_id.return_value = "other-host"
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "2-newkey"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"


async def test_options(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 60})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {CONF_SCAN_INTERVAL: 60}
