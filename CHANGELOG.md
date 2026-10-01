# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

Add entries under **Unreleased** in your change. On every push to `main` the release workflow moves them under a
new version heading, tags it and publishes a GitHub release. If Unreleased is empty, commit subjects are used.

## [Unreleased]

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
