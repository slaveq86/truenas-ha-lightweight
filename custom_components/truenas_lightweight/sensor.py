"""Sensors for TrueNAS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import ALERT_LEVELS, TASK_STATES, App, Pool, Task, TrueNASData
from .coordinator import TrueNASConfigEntry, TrueNASCoordinator
from .entity import (
    TASK_KINDS,
    TrueNASEntity,
    TrueNASTaskEntity,
    apps_device,
    async_track_items,
    pool_device,
    task_items,
)

POOL_STATUSES = ["online", "degraded", "faulted", "offline", "unavail", "removed"]
APP_STATES = ["running", "deploying", "stopping", "stopped", "crashed"]
ALERT_LEVEL_OPTIONS = ["ok", *(level.lower() for level in ALERT_LEVELS)]
TASK_STATE_OPTIONS = [state.lower() for state in TASK_STATES]


def _enum(value: str, options: list[str]) -> str | None:
    """Map an API value onto enum options; unknown values become unknown state."""
    value = value.lower()
    return value if value in options else None


def _highest_alert(data: TrueNASData) -> str:
    alerts = data.active_alerts
    return max(alerts, key=lambda a: a.severity).level.lower() if alerts else "ok"


def _alert_attrs(data: TrueNASData) -> dict[str, Any]:
    return {
        "alerts": [
            {"level": a.level, "klass": a.klass, "message": a.message}
            for a in sorted(data.active_alerts, key=lambda a: -a.severity)
        ]
    }


@dataclass(frozen=True, kw_only=True)
class TrueNASSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TrueNASData], Any]
    attrs_fn: Callable[[TrueNASData], dict[str, Any]] | None = None
    # Realtime stats arrive via subscription and may be briefly missing.
    requires_stats: bool = False
    # Sits on the Apps child device instead of the host.
    apps_device: bool = False


@dataclass(frozen=True, kw_only=True)
class TrueNASPoolSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Pool], Any]


@dataclass(frozen=True, kw_only=True)
class TrueNASAppSensorDescription(SensorEntityDescription):
    value_fn: Callable[[App], Any]


@dataclass(frozen=True, kw_only=True)
class TrueNASTaskSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Task], Any]
    attrs_fn: Callable[[Task], dict[str, Any]] | None = None


_BYTES = {
    "device_class": SensorDeviceClass.DATA_SIZE,
    "native_unit_of_measurement": UnitOfInformation.BYTES,
    "suggested_unit_of_measurement": UnitOfInformation.GIBIBYTES,
    "suggested_display_precision": 1,
}

SYSTEM_SENSORS: tuple[TrueNASSensorDescription, ...] = (
    TrueNASSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        requires_stats=True,
        value_fn=lambda d: d.stats.cpu_usage if d.stats else None,
    ),
    TrueNASSensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        requires_stats=True,
        value_fn=lambda d: d.stats.cpu_temp if d.stats else None,
    ),
    TrueNASSensorDescription(
        key="memory_used_pct",
        translation_key="memory_used_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        requires_stats=True,
        value_fn=lambda d: d.stats.mem_used_pct if d.stats else None,
    ),
    TrueNASSensorDescription(
        key="memory_used",
        translation_key="memory_used",
        state_class=SensorStateClass.MEASUREMENT,
        requires_stats=True,
        value_fn=lambda d: d.stats.mem_used if d.stats else None,
        **_BYTES,
    ),
    TrueNASSensorDescription(
        key="memory_available",
        translation_key="memory_available",
        state_class=SensorStateClass.MEASUREMENT,
        requires_stats=True,
        value_fn=lambda d: d.stats.mem_available if d.stats else None,
        **_BYTES,
    ),
    TrueNASSensorDescription(
        key="arc_size",
        translation_key="arc_size",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        requires_stats=True,
        value_fn=lambda d: d.stats.arc_size if d.stats else None,
        **_BYTES,
    ),
    *(
        TrueNASSensorDescription(
            key=f"load_{minutes}",
            translation_key=f"load_{minutes}",
            state_class=SensorStateClass.MEASUREMENT,
            suggested_display_precision=2,
            value_fn=lambda d, i=i: d.system.loadavg[i] if d.system.loadavg else None,
        )
        for i, minutes in enumerate((1, 5, 15))
    ),
    TrueNASSensorDescription(
        key="boot_time",
        translation_key="boot_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.boot_time,
    ),
    TrueNASSensorDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.system.version,
    ),
    TrueNASSensorDescription(
        key="active_alerts",
        translation_key="active_alerts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.active_alerts),
        attrs_fn=_alert_attrs,
    ),
    TrueNASSensorDescription(
        key="highest_alert_level",
        translation_key="highest_alert_level",
        device_class=SensorDeviceClass.ENUM,
        options=ALERT_LEVEL_OPTIONS,
        value_fn=_highest_alert,
    ),
    TrueNASSensorDescription(
        key="apps_running",
        translation_key="apps_running",
        apps_device=True,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(a.state == "RUNNING" for a in d.apps.values()),
    ),
)

POOL_SENSORS: tuple[TrueNASPoolSensorDescription, ...] = (
    TrueNASPoolSensorDescription(
        key="status",
        translation_key="pool_status",
        device_class=SensorDeviceClass.ENUM,
        options=POOL_STATUSES,
        value_fn=lambda p: _enum(p.status, POOL_STATUSES),
    ),
    TrueNASPoolSensorDescription(
        key="used_pct",
        translation_key="pool_used_pct",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda p: p.used_pct,
    ),
    TrueNASPoolSensorDescription(
        key="free",
        translation_key="pool_free",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.free,
        **_BYTES,
    ),
)

APP_SENSORS: tuple[TrueNASAppSensorDescription, ...] = (
    TrueNASAppSensorDescription(
        key="state",
        translation_key="app_state",
        device_class=SensorDeviceClass.ENUM,
        options=APP_STATES,
        value_fn=lambda a: _enum(a.state, APP_STATES),
    ),
)


def _task_sensors(kind: str) -> tuple[TrueNASTaskSensorDescription, ...]:
    return (
        TrueNASTaskSensorDescription(
            key="status",
            translation_key=f"{kind}_status",
            device_class=SensorDeviceClass.ENUM,
            options=TASK_STATE_OPTIONS,
            value_fn=lambda t: _enum(t.state, TASK_STATE_OPTIONS),
            attrs_fn=lambda t: {"enabled": t.enabled, "error": t.error, **t.details},
        ),
        TrueNASTaskSensorDescription(
            key="last_run",
            translation_key=f"{kind}_last_run",
            device_class=SensorDeviceClass.TIMESTAMP,
            value_fn=lambda t: t.last_run,
        ),
    )


TASK_SENSORS = {kind: _task_sensors(kind) for kind in TASK_KINDS}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TrueNASConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TrueNASSystemSensor(coordinator, d) for d in SYSTEM_SENSORS)
    async_track_items(
        coordinator,
        lambda data: data.pools,
        lambda name: (TrueNASPoolSensor(coordinator, d, name) for d in POOL_SENSORS),
        async_add_entities,
    )
    async_track_items(
        coordinator,
        lambda data: data.apps,
        lambda name: (TrueNASAppSensor(coordinator, d, name) for d in APP_SENSORS),
        async_add_entities,
    )
    for kind, descriptions in TASK_SENSORS.items():
        async_track_items(
            coordinator,
            lambda data, kind=kind: task_items(data, kind),
            lambda task_id, kind=kind, descriptions=descriptions: (
                TrueNASTaskSensor(coordinator, d, kind, task_id) for d in descriptions
            ),
            async_add_entities,
        )


class TrueNASSystemSensor(TrueNASEntity, SensorEntity):
    entity_description: TrueNASSensorDescription

    def __init__(self, coordinator: TrueNASCoordinator, description: TrueNASSensorDescription) -> None:
        super().__init__(coordinator, description.key, apps_device(coordinator) if description.apps_device else None)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return super().available and (
            not self.entity_description.requires_stats or self.coordinator.data.stats is not None
        )

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class TrueNASPoolSensor(TrueNASEntity, SensorEntity):
    entity_description: TrueNASPoolSensorDescription

    def __init__(self, coordinator: TrueNASCoordinator, description: TrueNASPoolSensorDescription, pool: str) -> None:
        super().__init__(coordinator, f"pool_{pool}_{description.key}", pool_device(coordinator, pool))
        self.entity_description = description
        self._pool = pool

    @property
    def available(self) -> bool:
        return super().available and self._pool in self.coordinator.data.pools

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data.pools[self._pool])


class TrueNASAppSensor(TrueNASEntity, SensorEntity):
    entity_description: TrueNASAppSensorDescription

    def __init__(self, coordinator: TrueNASCoordinator, description: TrueNASAppSensorDescription, app: str) -> None:
        super().__init__(coordinator, f"app_{app}_{description.key}", apps_device(coordinator))
        self.entity_description = description
        self._app = app
        self._attr_translation_placeholders = {"app": app}

    @property
    def available(self) -> bool:
        return super().available and self._app in self.coordinator.data.apps

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data.apps[self._app])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        app = self.coordinator.data.apps[self._app]
        return {"version": app.version}


class TrueNASTaskSensor(TrueNASTaskEntity, SensorEntity):
    entity_description: TrueNASTaskSensorDescription

    def __init__(
        self, coordinator: TrueNASCoordinator, description: TrueNASTaskSensorDescription, kind: str, task_id: str
    ) -> None:
        super().__init__(coordinator, description.key, kind, task_id)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.task)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.task)
