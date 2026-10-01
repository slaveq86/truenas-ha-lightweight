"""Config flow for TrueNAS Lightweight."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import TrueNASAuthError, TrueNASClient, TrueNASError, TrueNASPermissionError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import TrueNASConfigEntry

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
        vol.Required(CONF_VERIFY_SSL, default=True): bool,
        vol.Required(CONF_API_KEY): str,
    }
)
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


def _split_host(value: str) -> tuple[str, int | None]:
    """Accept a host, host:port or pasted URL (e.g. https://nas.local:8443/ui).

    Returns the bare host (IPv6 without brackets) and the port if one was given.
    Raises ValueError for unparsable input.
    """
    value = value.strip()
    with suppress(ValueError):
        return str(ip_address(value)), None  # bare IP, incl. unbracketed IPv6
    parts = urlsplit(value if "://" in value else f"//{value}")
    if not parts.hostname:
        raise ValueError(value)
    return parts.hostname, parts.port


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> tuple[str, str]:
    """Connect with the given settings; return (host_id, hostname)."""
    client = TrueNASClient(
        async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL]),
        data[CONF_HOST],
        data[CONF_API_KEY],
        data[CONF_PORT],
        subscribe_realtime=False,
    )
    try:
        host_id = await client.host_id()
        info = await client.system_info()
    finally:
        await client.close()
    return host_id, info.hostname


class TrueNASConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TrueNAS."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host, port = _split_host(user_input[CONF_HOST])
            except ValueError:
                errors = {CONF_HOST: "invalid_host"}
            else:
                user_input[CONF_HOST] = host
                if port is not None:
                    user_input[CONF_PORT] = port
                host_id, hostname, errors = await self._try_validate(user_input)
            if not errors:
                await self.async_set_unique_id(host_id)
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: user_input[CONF_HOST], CONF_PORT: user_input[CONF_PORT]}
                )
                return self.async_create_entry(title=hostname, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, **user_input}
            host_id, _, errors = await self._try_validate(data)
            if not errors:
                await self.async_set_unique_id(host_id)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def _try_validate(self, data: Mapping[str, Any]) -> tuple[str, str, dict[str, str]]:
        try:
            host_id, hostname = await _validate(self.hass, data)
        except TrueNASAuthError:
            return "", "", {"base": "invalid_auth"}
        except TrueNASPermissionError:
            return "", "", {"base": "insufficient_permissions"}
        except TrueNASError:
            return "", "", {"base": "cannot_connect"}
        except Exception:
            LOGGER.exception("Unexpected error validating TrueNAS connection")
            return "", "", {"base": "unknown"}
        return host_id, hostname, {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: TrueNASConfigEntry) -> TrueNASOptionsFlow:
        return TrueNASOptionsFlow()


class TrueNASOptionsFlow(OptionsFlow):
    """Options: polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
