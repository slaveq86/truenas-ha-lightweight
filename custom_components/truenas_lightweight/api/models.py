"""Typed views over TrueNAS API payloads."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Ordered from least to most severe, as defined by TrueNAS AlertLevel.
ALERT_LEVELS = ["INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY"]

# Last-run states shared by rsync (job states) and snapshot tasks (zettarepl states, normalised).
TASK_STATES = ["PENDING", "WAITING", "RUNNING", "SUCCESS", "FAILED", "ABORTED", "HOLD"]
_TASK_STATE_ALIASES = {"FINISHED": "SUCCESS", "ERROR": "FAILED"}

# Filler that board vendors leave in DMI fields (system_manufacturer, system_serial, ...), compared lower-cased.
_DMI_PLACEHOLDERS = {
    "",
    "0",
    "0123456789",
    "default string",
    "none",
    "not applicable",
    "not specified",
    "o.e.m.",
    "oem",
    "rev x.0x",
    "system manufacturer",
    "system product name",
    "system serial number",
    "system version",
    "to be filled by o.e.m.",
}


def _clean_dmi(value: Any) -> str | None:
    if not isinstance(value, str) or value.strip().lower() in _DMI_PLACEHOLDERS:
        return None
    return value.strip()


def _number(value: Any) -> float | None:
    """A finite int/float (bools, NaN and infinity rejected), else None."""
    if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def _mean(values: Iterable[Any]) -> float | None:
    """Average of the numeric values, ignoring missing ones."""
    numbers = [n for v in values if (n := _number(v)) is not None]
    return sum(numbers) / len(numbers) if numbers else None


def _dig(data: Any, *keys: str) -> Any:
    """data[k1][k2]..., or None as soon as a level is missing or not a dict."""
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _round(value: float | None, digits: int | None = 1) -> Any:
    return None if value is None else round(value, digits)


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
    manufacturer: str | None = None
    product_version: str | None = None
    serial: str | None = None
    ecc_memory: bool | None = None
    build_time: datetime | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> SystemInfo:
        uptime = data.get("uptime_seconds")
        boot_time = _parse_date(data.get("boottime"))
        if boot_time is None and uptime is not None:
            # Jitters by poll latency; the coordinator keeps the previous value stable.
            boot_time = (datetime.now(UTC) - timedelta(seconds=uptime)).replace(microsecond=0)
        loadavg = data.get("loadavg")
        ecc = data.get("ecc_memory")
        return cls(
            hostname=data.get("hostname") or "TrueNAS",
            version=data.get("version") or "unknown",
            # "model" in system.info is the CPU model, not the hardware.
            model=_clean_dmi(data.get("system_product")),
            cores=data.get("cores"),
            physmem=data.get("physmem"),
            uptime_seconds=uptime,
            boot_time=boot_time,
            loadavg=tuple(loadavg[:3]) if loadavg and len(loadavg) >= 3 else None,
            manufacturer=_clean_dmi(data.get("system_manufacturer")),
            product_version=_clean_dmi(data.get("system_product_version")),
            serial=_clean_dmi(data.get("system_serial")),
            ecc_memory=ecc if isinstance(ecc, bool) else None,
            build_time=_parse_date(data.get("buildtime")),
        )


def _cpu_total(fields: dict[str, Any]) -> dict[str, Any]:
    cpu = fields.get("cpu")
    # 24.10+ exposes the aggregate under "cpu"; older builds used "average".
    total = (cpu.get("cpu") or cpu.get("average")) if isinstance(cpu, dict) else None
    return total if isinstance(total, dict) else {}


@dataclass(slots=True)
class Interface:
    """A network interface from the reporting.realtime event; rates in bytes/s, averaged over the window."""

    name: str
    link_up: bool
    speed: int | None
    rx_rate: float | None
    tx_rate: float | None


@dataclass(slots=True, frozen=True)
class RealtimeSample:
    """The averaged numbers of one reporting.realtime frame: all that is kept of a frame until it is read."""

    cpu_usage: float | None
    disk_read: float | None
    disk_write: float | None
    disk_busy: float | None
    arc_hits: float
    arc_accesses: float
    # name -> (received, sent) bytes/s
    interfaces: dict[str, tuple[float | None, float | None]]

    @classmethod
    def from_realtime(cls, fields: dict[str, Any]) -> RealtimeSample:
        interfaces = fields.get("interfaces")
        return cls(
            cpu_usage=_number(_cpu_total(fields).get("usage")),
            disk_read=_number(_dig(fields, "disks", "read_bytes")),
            disk_write=_number(_dig(fields, "disks", "write_bytes")),
            disk_busy=_number(_dig(fields, "disks", "busy")),
            arc_hits=(_number(_dig(fields, "zfs", "demand_data_hits_per_second")) or 0)
            + (_number(_dig(fields, "zfs", "demand_metadata_hits_per_second")) or 0),
            arc_accesses=_number(_dig(fields, "zfs", "demand_accesses_per_second")) or 0,
            interfaces={
                name: (_number(iface.get("received_bytes_rate")), _number(iface.get("sent_bytes_rate")))
                for name, iface in (interfaces.items() if isinstance(interfaces, dict) else ())
                if isinstance(name, str) and isinstance(iface, dict)
            },
        )


@dataclass(slots=True)
class Stats:
    """reporting.realtime frames since the last read: gauges from the latest frame, rates averaged over all.

    The event is published every ~2 s but read once per update interval, so a single frame would make rates
    (CPU, network, disk I/O) jump around with whatever happened in that one 2 s slice.
    """

    cpu_usage: float | None = None
    cpu_temp: float | None = None
    mem_total: int | None = None
    mem_available: int | None = None
    arc_size: int | None = None
    # Aggregated over all disks by TrueNAS: bytes/s, and the average % of time disks were busy.
    disk_read_rate: float | None = None
    disk_write_rate: float | None = None
    disk_busy: float | None = None
    arc_hit_ratio: float | None = None
    interfaces: dict[str, Interface] = field(default_factory=dict)

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
    def from_realtime(
        cls,
        latest: dict[str, Any],
        window: Sequence[RealtimeSample] = (),
        previous_arc_hit_ratio: float | None = None,
    ) -> Stats:
        """Build from the latest frame and the samples of the frames since the previous read (`latest` if none).

        With no ARC reads in the window (idle system) there is no hit ratio to compute, so the previous one is kept.
        """
        window = window or (RealtimeSample.from_realtime(latest),)
        memory = latest.get("memory")
        memory = memory if isinstance(memory, dict) else {}
        interfaces_raw = latest.get("interfaces")

        accesses = sum(sample.arc_accesses for sample in window)
        hits = sum(sample.arc_hits for sample in window)
        arc_hit_ratio = round(min(hits / accesses, 1) * 100, 1) if accesses > 0 else previous_arc_hit_ratio

        interfaces = {}
        for name, iface in interfaces_raw.items() if isinstance(interfaces_raw, dict) else ():
            if not isinstance(name, str) or not isinstance(iface, dict):
                continue
            speed = _number(iface.get("speed"))
            rates = [sample.interfaces.get(name, (None, None)) for sample in window]
            interfaces[name] = Interface(
                name=name,
                link_up=iface.get("link_state") == "LINK_STATE_UP",
                speed=int(speed) if speed else None,
                rx_rate=_round(_mean(rx for rx, _ in rates), None),
                tx_rate=_round(_mean(tx for _, tx in rates), None),
            )

        return cls(
            cpu_usage=_round(_mean(sample.cpu_usage for sample in window)),
            cpu_temp=_round(_number(_cpu_total(latest).get("temp"))),
            mem_total=memory.get("physical_memory_total"),
            mem_available=memory.get("physical_memory_available"),
            arc_size=memory.get("arc_size"),
            disk_read_rate=_round(_mean(sample.disk_read for sample in window), None),
            disk_write_rate=_round(_mean(sample.disk_write for sample in window), None),
            disk_busy=_round(_mean(sample.disk_busy for sample in window)),
            arc_hit_ratio=arc_hit_ratio,
            interfaces=interfaces,
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
    # From the app.stats event, merged in by the coordinator.
    cpu_usage: float | None = None
    memory: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> App:
        return cls(
            name=data["name"],
            state=(data.get("state") or "UNKNOWN").upper(),
            version=data.get("human_version") or data.get("version"),
            upgrade_available=bool(data.get("upgrade_available")),
        )


# app name -> (cpu %, memory bytes) from one app.stats frame.
type AppStatsSample = dict[str, tuple[float | None, float | None]]


@dataclass(slots=True)
class AppStats:
    """Resource usage of one app from the app.stats event (stopped apps report 0)."""

    cpu_usage: float | None
    memory: int | None

    @staticmethod
    def sample(fields: list[Any]) -> AppStatsSample:
        """The numbers of one app.stats frame (a list of per-app dicts); malformed entries are skipped."""
        return {
            entry["app_name"]: (_number(entry.get("cpu_usage")), _number(entry.get("memory")))
            for entry in fields
            if isinstance(entry, dict) and isinstance(entry.get("app_name"), str)
        }

    @staticmethod
    def from_samples(window: Sequence[AppStatsSample]) -> dict[str, AppStats]:
        """Per app: CPU % averaged over the frames, memory (bytes) from the latest frame listing it."""
        cpu: dict[str, list[float | None]] = {}
        memory: dict[str, float | None] = {}
        for sample in window:
            for name, (cpu_usage, mem) in sample.items():
                cpu.setdefault(name, []).append(cpu_usage)
                memory[name] = mem
        return {
            name: AppStats(
                cpu_usage=_round(_mean(cpu[name])),
                memory=int(memory[name]) if memory[name] is not None else None,
            )
            for name in cpu
        }


@dataclass(slots=True)
class Service:
    """An entry from service.query (SMB, NFS, SSH, ...)."""

    name: str
    enabled: bool
    state: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Service:
        return cls(
            name=data["service"],
            # "enable" = start on boot, as toggled in the TrueNAS UI.
            enabled=bool(data.get("enable")),
            state=(data.get("state") or "UNKNOWN").upper(),
        )

    @property
    def running(self) -> bool:
        return self.state == "RUNNING"


@dataclass(slots=True)
class UpdateInfo:
    """Whether a newer TrueNAS release is available on the configured train."""

    available: bool
    version: str | None = None
    release_url: str | None = None
    # 25.04: an update was already applied and waits for a reboot.
    reboot_required: bool = False

    @classmethod
    def from_status(cls, data: dict[str, Any]) -> UpdateInfo:
        """update.status (25.10+): status.new_version is null when up to date."""
        new = _dig(data, "status", "new_version")
        if isinstance(new, dict) and isinstance(new.get("version"), str):
            return cls(True, new["version"], new.get("release_notes_url") or None)
        return cls(False)

    @classmethod
    def from_check_available(cls, data: dict[str, Any]) -> UpdateInfo:
        """update.check_available (25.04): status AVAILABLE, UNAVAILABLE, REBOOT_REQUIRED or HA_UNAVAILABLE."""
        status = data.get("status")
        if status == "AVAILABLE" and isinstance(data.get("version"), str):
            return cls(True, data["version"], data.get("release_notes_url") or None)
        return cls(False, reboot_required=status == "REBOOT_REQUIRED")


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
    services: dict[str, Service] = field(default_factory=dict)
    # None until the first successful check (or when the key may not read update status).
    update: UpdateInfo | None = None

    @property
    def active_alerts(self) -> list[Alert]:
        return [a for a in self.alerts if not a.dismissed]
