"""Base entity for TrueNAS."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from yarl import URL

from .api import Interface, Task, TrueNASData
from .const import DOMAIN
from .coordinator import TrueNASCoordinator


def child_device(
    coordinator: TrueNASCoordinator,
    suffix: str,
    translation_key: str,
    model: str,
    placeholders: dict[str, str] | None = None,
) -> DeviceInfo:
    """A device shown as connected via the TrueNAS host (pool, apps, data protection, network, services)."""
    host_id = coordinator.config_entry.unique_id
    return DeviceInfo(
        identifiers={(DOMAIN, f"{host_id}_{suffix}")},
        translation_key=translation_key,
        translation_placeholders={"host": coordinator.data.system.hostname, **(placeholders or {})},
        manufacturer="iXsystems",
        model=model,
        via_device=(DOMAIN, host_id),
    )


def pool_device(coordinator: TrueNASCoordinator, pool: str) -> DeviceInfo:
    return child_device(coordinator, f"pool_{pool}", "pool", "ZFS pool", {"pool": pool})


def apps_device(coordinator: TrueNASCoordinator) -> DeviceInfo:
    return child_device(coordinator, "apps", "apps", "Apps")


def data_protection_device(coordinator: TrueNASCoordinator) -> DeviceInfo:
    return child_device(coordinator, "data_protection", "data_protection", "Data protection")


def network_device(coordinator: TrueNASCoordinator) -> DeviceInfo:
    return child_device(coordinator, "network", "network", "Network")


def services_device(coordinator: TrueNASCoordinator) -> DeviceInfo:
    return child_device(coordinator, "services", "services", "Services")


class TrueNASEntity(CoordinatorEntity[TrueNASCoordinator]):
    """Entities sit on the TrueNAS host device unless given a child device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrueNASCoordinator, unique_suffix: str, device: DeviceInfo | None = None) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        system = coordinator.data.system
        self._attr_unique_id = f"{entry.unique_id}_{unique_suffix}"
        self._attr_device_info = device or DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=system.hostname,
            # Hardware vendor/model/serial from DMI; generic boards often leave them as placeholders.
            manufacturer=system.manufacturer or "iXsystems",
            model=system.model or "TrueNAS",
            hw_version=system.product_version,
            serial_number=system.serial,
            sw_version=system.version,
            configuration_url=str(URL.build(scheme="https", host=entry.data[CONF_HOST], port=entry.data[CONF_PORT])),
        )


TASK_KINDS = ("rsync", "snapshot")


def task_items(data: TrueNASData, kind: str) -> dict[str, Task]:
    """Tasks of one kind ("rsync" or "snapshot"), keyed by task id."""
    return data.rsync_tasks if kind == "rsync" else data.snapshot_tasks


class TrueNASTaskEntity(TrueNASEntity):
    """Entity bound to one rsync/snapshot task; unavailable once the task is deleted."""

    def __init__(self, coordinator: TrueNASCoordinator, key: str, kind: str, task_id: str) -> None:
        super().__init__(coordinator, f"{kind}_{task_id}_{key}", data_protection_device(coordinator))
        self._kind = kind
        self._task_id = task_id
        self._attr_translation_placeholders = {"task": self.task.name}

    @property
    def task(self) -> Task:
        return task_items(self.coordinator.data, self._kind)[self._task_id]

    @property
    def available(self) -> bool:
        return super().available and self._task_id in task_items(self.coordinator.data, self._kind)


def interface_items(data: TrueNASData) -> dict[str, Interface]:
    """Network interfaces from the realtime feed (none while it is missing)."""
    return data.stats.interfaces if data.stats else {}


class TrueNASInterfaceEntity(TrueNASEntity):
    """Entity bound to one network interface; unavailable while realtime stats are missing or it is gone."""

    def __init__(self, coordinator: TrueNASCoordinator, key: str, name: str) -> None:
        super().__init__(coordinator, f"interface_{name}_{key}", network_device(coordinator))
        self._name = name
        self._attr_translation_placeholders = {"interface": name}

    @property
    def interface(self) -> Interface:
        return interface_items(self.coordinator.data)[self._name]

    @property
    def available(self) -> bool:
        return super().available and self._name in interface_items(self.coordinator.data)


def async_track_items(
    coordinator: TrueNASCoordinator,
    items_fn: Callable[[TrueNASData], Iterable[str]],
    entities_fn: Callable[[str], Iterable[Entity]],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for pools/apps/tasks/disks/interfaces/services now and whenever new ones appear."""
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = set(items_fn(coordinator.data)) - known
        if not new:
            return
        known.update(new)
        async_add_entities([e for name in sorted(new) for e in entities_fn(name)])

    _add_new()
    coordinator.config_entry.async_on_unload(coordinator.async_add_listener(_add_new))
