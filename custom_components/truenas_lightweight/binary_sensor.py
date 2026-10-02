"""Binary sensors for TrueNAS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ALERT_LEVELS
from .coordinator import TrueNASConfigEntry, TrueNASCoordinator
from .entity import (
    TASK_KINDS,
    TrueNASEntity,
    TrueNASInterfaceEntity,
    TrueNASTaskEntity,
    apps_device,
    async_track_items,
    interface_items,
    pool_device,
    services_device,
    task_items,
)

WARNING_SEVERITY = ALERT_LEVELS.index("WARNING")

# service.query names -> what the TrueNAS UI calls them; anything else is upper-cased.
SERVICE_NAMES = {
    "cifs": "SMB",
    "ftp": "FTP",
    "iscsitarget": "iSCSI",
    "nfs": "NFS",
    "nvmet": "NVMe-oF",
    "smartd": "S.M.A.R.T.",
    "snmp": "SNMP",
    "ssh": "SSH",
    "ups": "UPS",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TrueNASConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TrueNASProblemSensor(coordinator), TrueNASEccSensor(coordinator)])
    async_track_items(
        coordinator,
        lambda data: data.pools,
        lambda name: [TrueNASPoolProblemSensor(coordinator, name)],
        async_add_entities,
    )
    async_track_items(
        coordinator,
        lambda data: data.apps,
        lambda name: [TrueNASAppUpdateSensor(coordinator, name)],
        async_add_entities,
    )
    async_track_items(
        coordinator,
        interface_items,
        lambda name: [TrueNASInterfaceLinkSensor(coordinator, name)],
        async_add_entities,
    )
    async_track_items(
        coordinator,
        lambda data: data.services,
        lambda name: [TrueNASServiceSensor(coordinator, name)],
        async_add_entities,
    )
    for kind in TASK_KINDS:
        async_track_items(
            coordinator,
            lambda data, kind=kind: task_items(data, kind),
            lambda task_id, kind=kind: [TrueNASTaskProblemSensor(coordinator, kind, task_id)],
            async_add_entities,
        )


class TrueNASProblemSensor(TrueNASEntity, BinarySensorEntity):
    """On when any active alert is WARNING or more severe."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, coordinator: TrueNASCoordinator) -> None:
        super().__init__(coordinator, "problem")

    @property
    def is_on(self) -> bool:
        return any(a.severity >= WARNING_SEVERITY for a in self.coordinator.data.active_alerts)


class TrueNASEccSensor(TrueNASEntity, BinarySensorEntity):
    """On when the host has ECC memory."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "ecc_memory"

    def __init__(self, coordinator: TrueNASCoordinator) -> None:
        super().__init__(coordinator, "ecc_memory")

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.system.ecc_memory is not None

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.system.ecc_memory


class TrueNASInterfaceLinkSensor(TrueNASInterfaceEntity, BinarySensorEntity):
    """On while the interface has a link."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "interface_link"

    def __init__(self, coordinator: TrueNASCoordinator, name: str) -> None:
        super().__init__(coordinator, "link", name)

    @property
    def is_on(self) -> bool:
        return self.interface.link_up

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"speed_mbps": self.interface.speed}


class TrueNASServiceSensor(TrueNASEntity, BinarySensorEntity):
    """On while a TrueNAS service (SMB, NFS, SSH, ...) runs; services not started on boot are disabled by default."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "service"

    def __init__(self, coordinator: TrueNASCoordinator, service: str) -> None:
        super().__init__(coordinator, f"service_{service}", services_device(coordinator))
        self._service = service
        self._attr_translation_placeholders = {"service": SERVICE_NAMES.get(service, service.upper())}
        self._attr_entity_registry_enabled_default = coordinator.data.services[service].enabled

    @property
    def available(self) -> bool:
        return super().available and self._service in self.coordinator.data.services

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.services[self._service].running

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"enabled": self.coordinator.data.services[self._service].enabled}


class TrueNASPoolProblemSensor(TrueNASEntity, BinarySensorEntity):
    """On when ZFS reports the pool as unhealthy."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "pool_problem"

    def __init__(self, coordinator: TrueNASCoordinator, pool: str) -> None:
        super().__init__(coordinator, f"pool_{pool}_problem", pool_device(coordinator, pool))
        self._pool = pool

    @property
    def available(self) -> bool:
        return super().available and self._pool in self.coordinator.data.pools

    @property
    def is_on(self) -> bool:
        return not self.coordinator.data.pools[self._pool].healthy


class TrueNASAppUpdateSensor(TrueNASEntity, BinarySensorEntity):
    """On when TrueNAS reports a newer catalog version of the app."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_translation_key = "app_update"

    def __init__(self, coordinator: TrueNASCoordinator, app: str) -> None:
        super().__init__(coordinator, f"app_{app}_update", apps_device(coordinator))
        self._app = app
        self._attr_translation_placeholders = {"app": app}

    @property
    def available(self) -> bool:
        return super().available and self._app in self.coordinator.data.apps

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.apps[self._app].upgrade_available


class TrueNASTaskProblemSensor(TrueNASTaskEntity, BinarySensorEntity):
    """On when the task's last run failed or was aborted, or the task is on hold."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: TrueNASCoordinator, kind: str, task_id: str) -> None:
        self._attr_translation_key = f"{kind}_problem"
        super().__init__(coordinator, "problem", kind, task_id)

    @property
    def is_on(self) -> bool:
        return self.task.problem

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"error": self.task.error}
