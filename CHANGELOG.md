# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

Add entries under **Unreleased** in your change. On every push to `main` the release workflow moves them under a
new version heading, tags it and publishes a GitHub release. If Unreleased is empty, commit subjects are used.

## [Unreleased]

### Added

- Network interface entities on a new *Network* device: download and upload rate, and a link sensor with the link
  speed, from the realtime feed.
- Disk read/write rate, disk busy and ZFS ARC hit ratio sensors.
- Read-only *Update* entity for TrueNAS itself (no install button). It checks every 6 hours and right after the
  version changes, using `update.status` on 25.10+ and `update.check_available` on 25.04.
- Service sensors (SMB, NFS, SSH, …) on a new *Services* device. They show whether each service is running, and
  services that TrueNAS doesn't start on boot are disabled by default.
- Per-app CPU usage and memory sensors from the `app.stats` feed.
- ECC memory diagnostic sensor and a `build_time` attribute on *Version*.
- Example dashboard: update status, disk I/O and ARC gauges and graph, Network and Services groups, and CPU/memory
  columns in the apps table.

### Changed

- Example dashboard layout: three independent columns instead of eight sections, so long lists no longer leave large
  gaps between sections on desktop. Small values (version, last boot, RAM, load, disk read/write) are heading badges,
  the alert list shows the 5 most severe, the data protection list the first 10 (problems first), services fit on
  one line, and the per-pool gauges and load graph are gone (the pools table already shows usage).
- The host device shows the hardware manufacturer, model, revision and serial number when the board reports them.
- CPU usage is averaged over the update interval instead of being a single 2-second sample.
- Diagnostics include services and the update status.

## [0.2.0] - 2026-10-01

### Added

- Disk temperature sensors, one per disk (on its pool's device; boot and unassigned disks on the host), refreshed
  every 5 minutes so polling doesn't keep HDDs from spinning down.
- Disks table and disk temperature graph in the example dashboard.

## [0.1.0] - 2026-10-01

### Added

- Example dashboard (`examples/dashboard.yaml`) covering system, storage, apps and data protection entities.

## [0.0.4] - 2026-10-01

- ssh keys (#3)
- Bump actions/setup-python from 6.3.0 to 7.0.0 (#1)
- Bump actions/checkout from 5.1.0 to 7.0.1 (#2)

## [0.0.3] - 2026-10-01

- Security hardening

## [0.0.2] - 2026-10-01

### Added

- Rsync task entities: status, last run time and a problem binary sensor (with the error message) per task.
- Periodic snapshot task entities: status, last run time and a problem binary sensor per task, named after the
  dataset and retention (e.g. `tank/photos (recursive, 2 weeks)`). Tasks on hold (e.g. locked dataset) count as a
  problem and show the reason.
- `truenas_lightweight_alert` event fired when an alert is raised or cleared (dismissing counts as cleared), for
  per-alert notifications. Existing alerts are not replayed at startup or when alert access is regained.

### Changed

- Entities are split into child devices connected via the TrueNAS host: one device per pool, one *Apps* device and
  one *Data protection* device for rsync and snapshot tasks. Existing entity ids are kept; app and task friendly
  names now start with the device name (e.g. *truenas Apps plex state*, *truenas Data protection Rsync … status*).
  Devices of deleted pools can be removed from the device page.
- Diagnostics now also redact pool, app and task names, datasets, rsync remotes, task errors and alert messages,
  which can contain paths, hostnames, IPs and disk serials.

### Fixed

- A malformed message from TrueNAS no longer leaves the connection stuck with every update timing out until the
  integration is reloaded; such messages are ignored, and any other reader failure triggers a reconnect.

## [0.0.1] - 2026-10-01

### Added

- Read-only TrueNAS integration over the JSON-RPC 2.0 WebSocket API (`wss://<host>/api/current`, TrueNAS 25.04+),
  authenticated with an API key of a Read-Only Admin user.
- Config flow with host/URL parsing (IPv6 supported), SSL verification toggle, reauthentication and
  configurable update interval.
- System sensors: CPU usage, CPU temperature, memory usage/used/available, ZFS ARC size, load averages,
  last boot and version.
- Alert sensors: active alert count with details, highest alert level and a problem binary sensor.
- Pool sensors: status, usage, free space and a problem binary sensor.
- App sensors: state per app, apps running count and an update-available binary sensor per app.
- Diagnostics download with redacted credentials and identifiers.
- HACS packaging with brand icons, CI (hassfest, HACS, ruff, pytest) and a development container.
