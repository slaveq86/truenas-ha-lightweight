# Integration package

## Data flow
`__init__.py` builds a `TrueNASClient` (HA session, `verify_ssl` from entry) → `TrueNASCoordinator` (`coordinator.py`) polls every `scan_interval` (options, default 30 s) with one `asyncio.gather` → `TrueNASData` (`api/models.py`) → entities read `coordinator.data`. `entry.runtime_data` is the coordinator; `TrueNASConfigEntry` type alias lives in `coordinator.py` to avoid circular imports.

## Disks
`disk.query` + `disk.temperatures` are fetched by `_fetch_disks` at most every `DISK_INTERVAL` (5 min, `const.py`) and cached between scans — temperature reads hit every disk and must not run each scan. Temperatures are merged into `Disk.temperature` by kernel name. Both go through `_best_effort` (non-auth errors → None, warned once): a failed `disk.query` keeps the cached disks, a failed `disk.temperatures` blanks them until the next attempt; the attempt time is set before fetching so failures back off too. Disk entities pick their device at creation, so a disk added to a pool moves only after a reload.

## Update, app stats, realtime
- The update check never blocks a refresh: `_schedule_update_check` starts `_check_update` as a config-entry background task (one at a time) every `UPDATE_INTERVAL` (6 h — on 25.04 it makes TrueNAS contact the iX update server), after a failed/inconclusive check every `UPDATE_RETRY` (30 min), and right away when `system.version` changed. The result is written into `self.data.update` + `async_update_listeners()`; failures keep the last `UpdateInfo` via `_best_effort` (takes the retry interval, shared `_errors` set). Tests that hold `update_info` open must not wait for background tasks (`_tick` does).
- `_merge_app_stats` copies `client.app_stats()` onto `App.cpu_usage/memory`. `_resubscribe_if_silent` calls `client.resubscribe(collection)` at most every `RESUBSCRIBE_INTERVAL` (5 min, first one 5 min after setup) while a push feed delivers nothing: `reporting.realtime` when stats are `None`, `app.stats` when it is `None` while an app is RUNNING. The client warns about failed subscriptions itself.
- `client.realtime_stats()` / `app_stats()` consume the frames since the previous read (rates averaged), so only the coordinator may call them, once per refresh. The coordinator hands the last ARC hit ratio back in, kept while the ARC is idle.

## Error mapping (coordinator)
- `TrueNASAuthError` → `ConfigEntryAuthFailed` (starts reauth flow).
- `TrueNASError` → `UpdateFailed`.
- `TrueNASPermissionError` on optional sources (alerts/pools/apps/rsync/snapshot tasks) → logged once, empty default; only `system.info` is mandatory. Wrap new optional sources in `_optional()`.

## Entities
- Devices: the host `(DOMAIN, host_id)` (manufacturer/model/hw_version/serial from DMI, iXsystems/TrueNAS fallback) plus child devices (`via_device` = host) built by `entity.child_device`: `{host_id}_pool_{name}` (one per pool), `{host_id}_apps`, `{host_id}_data_protection` (rsync + snapshot tasks), `{host_id}_network` (interfaces), `{host_id}_services`. Names are translated (`device` section in strings, `{host}` placeholder). Pass the device as the third `TrueNASEntity` arg; omitting it means the host. Never change device identifiers. `async_remove_config_entry_device` (`__init__.py`) only allows deleting devices of pools that no longer exist.
- Unique ids (independent of devices; `host_id` = entry `unique_id` = `system.host_id`): `{host_id}_{key}`, `{host_id}_pool_{name}_{key}`, `{host_id}_app_{name}_{key}`, `{host_id}_rsync_{task_id}_{key}`, `{host_id}_snapshot_{task_id}_{key}` (task ids, not names, since names are editable), `{host_id}_disk_{Disk.key}_{key}` (serial, not `sdX`, which changes between boots), `{host_id}_interface_{name}_{key}`, `{host_id}_service_{service}` (service.query name, e.g. `cifs`), `{host_id}_system_update` — never change these formats (breaks users' entity registry).
- Sensors are declared as `EntityDescription` tables in `sensor.py` (`value_fn` per description). Add new sensors there, not as new classes.
- Disk entities sit on their pool's device if the pool is in `data.pools` (not the boot pool), else on the host.
- Pools/apps/tasks/disks/interfaces/services are added dynamically via `entity.async_track_items`; task entities subclass `TrueNASTaskEntity` (kinds in `TASK_KINDS`), interface entities `TrueNASInterfaceEntity` (items from `data.stats`, so they go unavailable while stats are missing); per-item entities must override `available` to go unavailable when the item disappears.
- Service binary sensors are enabled by default only if the service starts on boot (`Service.enabled`), named via `SERVICE_NAMES` in `binary_sensor.py`.
- `update.py` is read-only: never set `supported_features` (no install) — that would need a mutating API call.
- Enum sensors: options are lowercase; map API values through `_enum()` so unknown values become `unknown` instead of raising.
- Every `translation_key` needs an entry in `strings.json` **and** `translations/en.json`.
- Realtime stats (`data.stats`) can be `None` — use `requires_stats=True` on descriptions that depend on it.

## Alert events
The coordinator diffs active (non-dismissed) alert uuids against `_alert_baseline` and fires `EVENT_ALERT` (`truenas_lightweight_alert`, `action: raised|cleared`). The baseline is `None` on the first refresh and after `alert.list` is denied (its `_optional` default is `None`, not `[]`); while `None` it is only re-seeded, never diffed, so neither startup, a denial nor regained access replays existing alerts. The event payload is public API for users' automations — only add fields.

## Config flow
User step validates with `subscribe_realtime=False`, splits host/URL input via `_split_host` (a port in a pasted URL overrides the Port field; IPv6 stored without brackets — the client adds them), unique id = host_id. Reauth replaces only the API key and aborts with `wrong_device` if host_id differs. Options change reloads the entry.

## Diagnostics
`diagnostics.py` redacts api_key, host, hostname, title, unique_id, name, path, dataset, remotehost, remote, error, message, serial (disk and system), identifier, pool, and turns the name-keyed pools/apps/task/disk/service dicts into lists (redaction only covers values, not keys). Redact any new identifying or free-text field you add to models.

`brand/icon.png` (256×256) and `icon@2x.png` (512×512) are required by HACS validation — a generic NAS icon; never use the TrueNAS/iXsystems logo.

This folder is shipped to users by HACS; keep it free of dev-only files other than this note.
