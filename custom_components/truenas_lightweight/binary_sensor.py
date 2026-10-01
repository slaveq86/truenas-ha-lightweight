"""Binary sensors for TrueNAS."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ALERT_LEVELS
from .coordinator import TrueNASConfigEntry, TrueNASCoordinator
from .entity import TASK_KINDS, TrueNASEntity, TrueNASTaskEntity, async_track_items, task_items

WARNING_SEVERITY = ALERT_LEVELS.index("WARNING")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TrueNASConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TrueNASProblemSensor(coordinator)])
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


class TrueNASPoolProblemSensor(TrueNASEntity, BinarySensorEntity):
    """On when ZFS reports the pool as unhealthy."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "pool_problem"

    def __init__(self, coordinator: TrueNASCoordinator, pool: str) -> None:
        super().__init__(coordinator, f"pool_{pool}_problem")
        self._pool = pool
        self._attr_translation_placeholders = {"pool": pool}

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
        super().__init__(coordinator, f"app_{app}_update")
        self._app = app
        self._attr_translation_placeholders = {"app": app}

    @property
    def available(self) -> bool:
        return super().available and self._app in self.coordinator.data.apps

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.apps[self._app].upgrade_available


class TrueNASTaskProblemSensor(TrueNASTaskEntity, BinarySensorEntity):
    """On when the task's last run failed or was aborted."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: TrueNASCoordinator, kind: str, task_id: str) -> None:
        self._attr_translation_key = f"{kind}_problem"
        super().__init__(coordinator, "problem", kind, task_id)

    @property
    def is_on(self) -> bool:
        return self.task.failed

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"error": self.task.error}
