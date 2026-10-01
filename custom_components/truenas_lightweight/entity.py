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

from .api import Task, TrueNASData
from .const import DOMAIN
from .coordinator import TrueNASCoordinator


class TrueNASEntity(CoordinatorEntity[TrueNASCoordinator]):
    """All entities belong to the single TrueNAS host device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrueNASCoordinator, unique_suffix: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        system = coordinator.data.system
        self._attr_unique_id = f"{entry.unique_id}_{unique_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=system.hostname,
            manufacturer="iXsystems",
            model=system.model or "TrueNAS",
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
        super().__init__(coordinator, f"{kind}_{task_id}_{key}")
        self._kind = kind
        self._task_id = task_id
        self._attr_translation_placeholders = {"task": self.task.name}

    @property
    def task(self) -> Task:
        return task_items(self.coordinator.data, self._kind)[self._task_id]

    @property
    def available(self) -> bool:
        return super().available and self._task_id in task_items(self.coordinator.data, self._kind)


def async_track_items(
    coordinator: TrueNASCoordinator,
    items_fn: Callable[[TrueNASData], Iterable[str]],
    entities_fn: Callable[[str], Iterable[Entity]],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add entities for pools/apps/tasks now and whenever new ones appear."""
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
