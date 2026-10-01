# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

Add entries under **Unreleased** in your change. On every push to `main` the release workflow moves them under a
new version heading, tags it and publishes a GitHub release. If Unreleased is empty, commit subjects are used.

## [Unreleased]

### Added

- Rsync task entities: status, last run time and a problem binary sensor (with the error message) per task.
- Periodic snapshot task entities: status, last run time and a problem binary sensor per task.
- `truenas_lightweight_alert` event fired when an alert is raised or cleared, for per-alert notifications.

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
