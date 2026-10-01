"""Typed views over TrueNAS API payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Ordered from least to most severe, as defined by TrueNAS AlertLevel.
ALERT_LEVELS = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]

# Last-run states shared by rsync (job states) and snapshot tasks (zettarepl states, normalised).
TASK_STATES = ["PENDING", "WAITING", "RUNNING", "SUCCESS", "FAILED", "ABORTED", "HOLD"]
_TASK_STATE_ALIASES = {"FINISHED": "SUCCESS", "ERROR": "FAILED"}


def _lifetime(value: Any, unit: Any) -> str | None:
    """Format snapshot retention, e.g. (2, "WEEK") -> "2 weeks"."""
    if not isinstance(value, int) or value <= 0 or not isinstance(unit, str) or not unit:
        return None
    return f"{value} {unit.lower()}{'' if value == 1 else 's'}"


def _parse_date(value: Any) -> datetime | None:
    """Parse TrueNAS JSON-RPC datetime encoding ({"$date": epoch_ms})."""
    if isinstance(value, dict) and isinstance(value.get("$date"), int | float):
        return datetime.fromtimestamp(value["$date"] / 1000, tz=UTC)
    return None


@dataclass(slots=True)
class SystemInfo:
    """Result of system.info."""

    hostname: str
    version: str
    model: str | None
    cores: int | None
    physmem: int | None
    uptime_seconds: float | None
    boot_time: datetime | None
    loadavg: tuple[float, float, float] | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SystemInfo:
        uptime = data.get("uptime_seconds")
        boot_time = _parse_date(data.get("boottime"))
        if boot_time is None and uptime is not None:
            # Jitters by poll latency; the coordinator keeps the previous value stable.
            boot_time = (datetime.now(UTC) - timedelta(seconds=uptime)).replace(microsecond=0)
        loadavg = data.get("loadavg")
        return cls(
            hostname=data.get("hostname") or "TrueNAS",
            version=data.get("version") or "unknown",
            # "model" in system.info is the CPU model, not the hardware.
            model=data.get("system_product"),
            cores=data.get("cores"),
            physmem=data.get("physmem"),
            uptime_seconds=uptime,
            boot_time=boot_time,
            loadavg=tuple(loadavg[:3]) if loadavg and len(loadavg) >= 3 else None,
        )


@dataclass(slots=True)
class Stats:
    """Latest snapshot of the reporting.realtime event."""

    cpu_usage: float | None = None
    cpu_temp: float | None = None
    mem_total: int | None = None
    mem_available: int | None = None
    arc_size: int | None = None

    @property
    def mem_used(self) -> int | None:
        if self.mem_total is None or self.mem_available is None:
            return None
        return self.mem_total - self.mem_available

    @property
    def mem_used_pct(self) -> float | None:
        used = self.mem_used
        if used is None or not self.mem_total:
            return None
        return round(used / self.mem_total * 100, 1)

    @classmethod
    def from_realtime(cls, fields: dict[str, Any]) -> Stats:
        cpu = fields.get("cpu") or {}
        # 24.10+ exposes the aggregate under "cpu"; older builds used "average".
        total = cpu.get("cpu") or cpu.get("average") or {}
        memory = fields.get("memory") or {}
        usage = total.get("usage")
        temp = total.get("temp")
        return cls(
            cpu_usage=round(usage, 1) if isinstance(usage, int | float) else None,
            cpu_temp=round(temp, 1) if isinstance(temp, int | float) else None,
            mem_total=memory.get("physical_memory_total"),
            mem_available=memory.get("physical_memory_available"),
            arc_size=memory.get("arc_size"),
        )


@dataclass(slots=True)
class Alert:
    """An entry from alert.list."""

    uuid: str
    klass: str
    level: str
    message: str
    dismissed: bool
    datetime: datetime | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Alert:
        return cls(
            uuid=data.get("uuid", ""),
            klass=data.get("klass", ""),
            level=(data.get("level") or "INFO").upper(),
            message=(data.get("formatted") or data.get("text") or "").strip(),
            dismissed=bool(data.get("dismissed")),
            datetime=_parse_date(data.get("datetime")),
        )

    @property
    def severity(self) -> int:
        try:
            return ALERT_LEVELS.index(self.level)
        except ValueError:
            return 0


@dataclass(slots=True)
class Pool:
    """An entry from pool.query."""

    name: str
    status: str
    healthy: bool
    size: int | None
    allocated: int | None
    free: int | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Pool:
        return cls(
            name=data["name"],
            status=(data.get("status") or "UNKNOWN").upper(),
            healthy=bool(data.get("healthy")),
            size=data.get("size"),
            allocated=data.get("allocated"),
            free=data.get("free"),
        )

    @property
    def used_pct(self) -> float | None:
        if not self.size or self.allocated is None:
            return None
        return round(self.allocated / self.size * 100, 1)


@dataclass(slots=True)
class App:
    """An entry from app.query."""

    name: str
    state: str
    version: str | None
    upgrade_available: bool

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> App:
        return cls(
            name=data["name"],
            state=(data.get("state") or "UNKNOWN").upper(),
            version=data.get("human_version") or data.get("version"),
            upgrade_available=bool(data.get("upgrade_available")),
        )


@dataclass(slots=True)
class Disk:
    """An entry from disk.query, with its temperature from disk.temperatures merged in."""

    name: str
    identifier: str | None
    serial: str | None
    model: str | None
    type: str | None
    size: int | None
    pool: str | None
    temperature: float | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Disk:
        return cls(
            name=data["name"],
            identifier=data.get("identifier") or None,
            serial=(data.get("serial") or "").strip() or None,
            model=(data.get("model") or "").strip() or None,
            type=(data.get("type") or "").upper() or None,
            size=data.get("size"),
            pool=data.get("pool") or None,
        )

    @property
    def key(self) -> str:
        """Stable id: kernel names (sda, ...) can change between boots, serials don't."""
        return self.serial or self.identifier or self.name


@dataclass(slots=True)
class Task:
    """A data protection task (rsync, periodic snapshot) and the outcome of its last run."""

    id: int
    name: str
    enabled: bool
    state: str
    last_run: datetime | None
    error: str | None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_rsync(cls, data: dict[str, Any]) -> Task:
        remote = data.get("remotemodule") if data.get("mode") == "MODULE" else data.get("remotepath")
        return cls._build(
            data,
            name=data.get("desc") or data.get("path") or f"Task {data['id']}",
            details={
                "direction": (data.get("direction") or "").lower() or None,
                "path": data.get("path"),
                "remotehost": data.get("remotehost"),
                "remote": remote,
            },
        )

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> Task:
        dataset = data.get("dataset") or f"Task {data['id']}"
        lifetime = _lifetime(data.get("lifetime_value"), data.get("lifetime_unit"))
        # Datasets commonly have several tasks (hourly/daily/...); retention tells them apart.
        qualifiers = [q for q in ("recursive" if data.get("recursive") else None, lifetime) if q]
        return cls._build(
            data,
            name=f"{dataset} ({', '.join(qualifiers)})" if qualifiers else dataset,
            details={
                "dataset": data.get("dataset"),
                "naming_schema": data.get("naming_schema"),
                "lifetime": lifetime,
            },
        )

    @classmethod
    def _build(cls, data: dict[str, Any], *, name: str, details: dict[str, Any]) -> Task:
        # Prefer the last job (rsync); fall back to the task state dict (snapshot tasks, older payloads).
        job = data.get("job") if isinstance(data.get("job"), dict) else None
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        if job:
            raw = job.get("state")
            last_run = _parse_date(job.get("time_finished")) or _parse_date(job.get("time_started"))
            error = job.get("error")
        else:
            raw = state.get("state")
            last_run = _parse_date(state.get("datetime"))
            # HOLD states explain themselves in "reason" rather than "error".
            error = state.get("error") or state.get("reason")
        raw = (raw or "PENDING").upper()
        return cls(
            id=data["id"],
            name=name,
            enabled=bool(data.get("enabled", True)),
            state=_TASK_STATE_ALIASES.get(raw, raw),
            last_run=last_run,
            error=error.strip() if isinstance(error, str) and error.strip() else None,
            details=details,
        )

    @property
    def problem(self) -> bool:
        """Last run failed or was aborted, or the task is held (e.g. its dataset is locked)."""
        return self.state in ("FAILED", "ABORTED", "HOLD")


@dataclass(slots=True)
class TrueNASData:
    """Everything the coordinator fetches in one poll."""

    system: SystemInfo
    stats: Stats | None = None
    alerts: list[Alert] = field(default_factory=list)
    pools: dict[str, Pool] = field(default_factory=dict)
    apps: dict[str, App] = field(default_factory=dict)
    rsync_tasks: dict[str, Task] = field(default_factory=dict)
    snapshot_tasks: dict[str, Task] = field(default_factory=dict)
    disks: dict[str, Disk] = field(default_factory=dict)

    @property
    def active_alerts(self) -> list[Alert]:
        return [a for a in self.alerts if not a.dismissed]
