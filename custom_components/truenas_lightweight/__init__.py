"""TrueNAS Lightweight: read-only TrueNAS integration over the JSON-RPC WebSocket API."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, CONF_HOST, CONF_PORT, CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceEntry

from .api import TrueNASClient
from .const import DOMAIN
from .coordinator import TrueNASConfigEntry, TrueNASCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR, Platform.UPDATE]


async def async_setup_entry(hass: HomeAssistant, entry: TrueNASConfigEntry) -> bool:
    """Set up TrueNAS from a config entry."""
    client = TrueNASClient(
        async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL]),
        entry.data[CONF_HOST],
        entry.data[CONF_API_KEY],
        entry.data[CONF_PORT],
    )
    coordinator = TrueNASCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TrueNASConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: TrueNASConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: TrueNASConfigEntry, device: DeviceEntry) -> bool:
    """Allow deleting the device of a pool that no longer exists; the host and grouped devices stay."""
    prefix = f"{entry.unique_id}_pool_"
    pools = entry.runtime_data.data.pools
    return any(
        domain == DOMAIN and ident.startswith(prefix) and ident.removeprefix(prefix) not in pools
        for domain, ident in device.identifiers
    )
