# TrueNAS Lightweight for Home Assistant

A lightweight, **read-only** Home Assistant integration for TrueNAS SCALE / Community Edition.

- Talks to the current TrueNAS **JSON-RPC 2.0 WebSocket API** (`wss://<host>/api/current`) — no deprecated REST.
- Authenticates with an **API key of a read-only user**; it never calls a method that changes anything.
- No external Python dependencies, one persistent connection, one parallel poll per interval.

Requires **TrueNAS 25.04 or newer** and **Home Assistant 2025.2 or newer**.

## Entities

Entities are grouped into devices; the child devices show as *connected via* the TrueNAS host:

| Device | Entities |
|---|---|
| *hostname* (the TrueNAS host) | system, disk I/O, alert, update and overall problem entities; shows the hardware manufacturer, model and serial |
| *hostname* Pool *name* (one per pool) | pool status, usage, free space, problem |
| *hostname* Apps | apps running, and state, CPU, memory + update per app |
| *hostname* Data protection | rsync and periodic snapshot tasks |
| *hostname* Network | download, upload and link per network interface |
| *hostname* Services | one running sensor per TrueNAS service (SMB, NFS, SSH, …) |

| Entity | Device | Type | Notes |
|---|---|---|---|
| CPU usage | host | sensor (%) | from the `reporting.realtime` feed, averaged over the update interval |
| CPU temperature | host | sensor (°C) | disabled by default; not available on every host |
| Memory usage / used / available | host | sensor (%, GiB) | |
| ZFS ARC size | host | sensor (GiB) | disabled by default |
| ZFS ARC hit ratio | host | sensor (%) | share of ARC reads served from memory; keeps its value while the pool is idle |
| Disk read rate / write rate | host | sensor (MB/s) | all disks combined |
| Disk busy | host | sensor (%) | average share of time the disks were busy |
| ECC memory | host | binary sensor | diagnostic |
| Load (1/5/15 min) | host | sensor | |
| Last boot | host | timestamp | diagnostic |
| Version | host | sensor | diagnostic; `build_time` attribute |
| Update | host | update | newer TrueNAS release on the configured train; read-only (no install button), checked every 6 h and right after the version changes |
| Active alerts | host | sensor (count) | `alerts` attribute lists level, class and message |
| Highest alert level | host | enum sensor | `ok`, `info` … `emergency` |
| Problem | host | binary sensor | on when any active alert is WARNING or worse |
| Status | pool | enum sensor | `online`, `degraded`, `faulted`, … |
| Usage / Free space | pool | sensor (%, GiB) | |
| Problem | pool | binary sensor | on when ZFS reports the pool unhealthy |
| Running | Apps | sensor (count) | number of running apps |
| *app* state | Apps | enum sensor | `running`, `deploying`, `stopping`, `stopped`, `crashed` |
| *app* update | Apps | binary sensor | on when a newer catalog version is available |
| *app* CPU usage / memory | Apps | sensor (%, MiB) | from the `app.stats` feed; 0 for stopped apps |
| *interface* download / upload | Network | sensor (Mbit/s) | averaged over the update interval |
| *interface* link | Network | binary sensor | on while the link is up; `speed_mbps` attribute |
| *service* | Services | binary sensor | on while running; `enabled` attribute (start on boot). Services not set to start on boot are disabled by default |
| Rsync *task* status | Data protection | enum sensor | `pending`, `waiting`, `running`, `success`, `failed`, `aborted`, `hold`; attributes: direction, path, remote, error |
| Rsync *task* last run | Data protection | timestamp | when the last run finished |
| Rsync *task* problem | Data protection | binary sensor | on when the last run failed or was aborted, or the task is on hold; `error` attribute |
| Snapshot *dataset (retention)* status / last run / problem | Data protection | as above | periodic snapshot tasks; attributes: naming schema, lifetime |

New pools, apps, tasks, interfaces and services are picked up automatically; removed ones become unavailable (the device of a deleted pool
can then be deleted from its device page). Rsync tasks are named after
their description (or path if empty), snapshot tasks after their dataset and retention, e.g.
*Snapshot tank/photos (recursive, 2 weeks) status*, so several tasks on one dataset stay distinguishable.

## Alert events

Whenever an alert appears in or disappears from TrueNAS, the integration fires a `truenas_lightweight_alert` event
(checked every update interval; nothing is fired for alerts already active when Home Assistant starts).
Dismissing an alert in TrueNAS counts as `cleared`, restoring it as `raised`:

```yaml
event_type: truenas_lightweight_alert
data:
  action: raised            # or "cleared"
  config_entry_id: 01J...
  hostname: truenas
  uuid: 6f1c...
  level: WARNING            # INFO, NOTICE, WARNING, ERROR, CRITICAL, ALERT, EMERGENCY
  klass: ZpoolCapacityWarning
  message: Space usage for pool "tank" is 81%.
  datetime: "2025-10-01T06:26:40+00:00"
```

Example automation that pushes every new alert of level WARNING or worse to your phone:

```yaml
automation:
  - alias: TrueNAS alert notification
    triggers:
      - trigger: event
        event_type: truenas_lightweight_alert
        event_data:
          action: raised
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.level not in ['INFO', 'NOTICE'] }}"
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: "TrueNAS {{ trigger.event.data.level | lower }}"
          message: "{{ trigger.event.data.message }}"
```

## Dashboard

[`examples/dashboard.yaml`](examples/dashboard.yaml) is a ready-made dashboard with health, alerts and the TrueNAS
update, CPU/memory/disk gauges and graphs, and compact lists of pools, disks, network interfaces, apps (state, CPU,
memory, version, updates), services and rsync/snapshot tasks that pick up new items automatically. It is laid out as
three independent columns, so a long alert or disk list doesn't leave gaps next to it; the alert and task lists are
capped (problems first). The disk temperature and network graphs need the
[auto-entities](https://github.com/thomasloven/lovelace-auto-entities) card (HACS → Frontend). Host entity ids assume the hostname `truenas`; find/replace `sensor.truenas_` and
`binary_sensor.truenas_` if yours differs. Paste it via a new dashboard's **Edit → ⋮ → Raw configuration editor**.

## Creating a read-only API key

1. Create a user (e.g. `homeassistant`) in **Credentials → Users → Add**; no shell, SMB or home directory needed.
2. Give it the **Readonly Admin** role:
   - **25.10+**: in the user form, under *TrueNAS Access*, pick **Read-Only Admin**.
   - **25.04**: create a group (e.g. `ha-readonly`) and add the user to it, then in **Credentials → Groups → Privileges → Add** create a privilege with role **Readonly Admin** for that group.
3. Open **Credentials → API Keys → Add**, select that user and copy the key (it is shown only once).

> TrueNAS automatically revokes API keys that are sent over unencrypted connections, so this integration always uses `wss://`. If your NAS uses the default self-signed certificate, untick *Verify SSL certificate* during setup.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/slaveq86/truenas-ha-lightweight` as *Integration*.
2. Install **TrueNAS Lightweight**, restart Home Assistant.
3. **Settings → Devices & services → Add integration → TrueNAS Lightweight**.

### Manual

Copy `custom_components/truenas_lightweight` into your Home Assistant `config/custom_components/` and restart.

## Configuration

| Field | Default | |
|---|---|---|
| Host | — | hostname or IP; a pasted URL is fine and its port overrides *Port* |
| Port | 443 | HTTPS port of the web UI |
| Verify SSL certificate | on | turn off for self-signed certs |
| API key | — | key of the read-only user |

Options: **Update interval** (10–600 s, default 30 s).

The integration keeps two push subscriptions open on its connection: `reporting.realtime` (CPU, memory, network, disk
I/O, ARC) and `app.stats` (per-app CPU/memory). Disk temperatures are read every 5 minutes. The update check runs every
6 hours: on TrueNAS 25.10+ it reads `update.status`, while on 25.04 it calls `update.check_available`, which makes
TrueNAS ask the iX update server.

If the key is revoked, Home Assistant asks you to re-authenticate. If the user lacks permission for some calls (e.g. apps), those entities are skipped and a warning is logged instead of failing the whole integration.

## Development

```bash
scripts/setup      # install dev deps + pre-commit hooks
scripts/lint       # ruff format + lint
pytest             # unit tests (mocked TrueNAS + fake JSON-RPC server)
scripts/develop    # run Home Assistant on http://localhost:8123 with this integration
```

### Releases

Every push to `main` is tested and released automatically: the version is bumped, `CHANGELOG.md` updated, a `vX.Y.Z` tag and GitHub release created. Add your change under `## [Unreleased]` in `CHANGELOG.md`; use `feat:` / `#minor` for a minor bump and `#major` / `BREAKING CHANGE` for a major one (default is patch). `python scripts/release.py --dry-run` shows what would be released.

A VS Code dev container (`.devcontainer/`) is included and runs `scripts/setup` automatically.

```
custom_components/truenas_lightweight/
  api/            # standalone async JSON-RPC WebSocket client + typed models
  coordinator.py  # DataUpdateCoordinator: one parallel poll per interval
  sensor.py, binary_sensor.py, config_flow.py, diagnostics.py
tests/            # pytest-homeassistant-custom-component tests + API fixtures
```

## License

Apache-2.0
