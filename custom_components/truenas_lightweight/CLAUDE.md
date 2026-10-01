# Integration package

## Data flow
`__init__.py` builds a `TrueNASClient` (HA session, `verify_ssl` from entry) → `TrueNASCoordinator` (`coordinator.py`) polls every `scan_interval` (options, default 30 s) with one `asyncio.gather` → `TrueNASData` (`api/models.py`) → entities read `coordinator.data`. `entry.runtime_data` is the coordinator; `TrueNASConfigEntry` type alias lives in `coordinator.py` to avoid circular imports.

## Error mapping (coordinator)
- `TrueNASAuthError` → `ConfigEntryAuthFailed` (starts reauth flow).
- `TrueNASError` → `UpdateFailed`.
- `TrueNASPermissionError` on optional sources (alerts/pools/apps/rsync/snapshot tasks) → logged once, empty default; only `system.info` is mandatory. Wrap new optional sources in `_optional()`.

## Entities
- Devices: the host `(DOMAIN, host_id)` plus child devices (`via_device` = host) built by `entity.child_device`: `{host_id}_pool_{name}` (one per pool), `{host_id}_apps`, `{host_id}_data_protection` (rsync + snapshot tasks). Names are translated (`device` section in strings, `{host}` placeholder). Pass the device as the third `TrueNASEntity` arg; omitting it means the host. Never change device identifiers. `async_remove_config_entry_device` (`__init__.py`) only allows deleting devices of pools that no longer exist.
- Unique ids (independent of devices; `host_id` = entry `unique_id` = `system.host_id`): `{host_id}_{key}`, `{host_id}_pool_{name}_{key}`, `{host_id}_app_{name}_{key}`, `{host_id}_rsync_{task_id}_{key}`, `{host_id}_snapshot_{task_id}_{key}` (task ids, not names, since names are editable) — never change these formats (breaks users' entity registry).
- Sensors are declared as `EntityDescription` tables in `sensor.py` (`value_fn` per description). Add new sensors there, not as new classes.
- Pools/apps/tasks are added dynamically via `entity.async_track_items`; task entities subclass `TrueNASTaskEntity` (kinds in `TASK_KINDS`); per-item entities must override `available` to go unavailable when the item disappears.
- Enum sensors: options are lowercase; map API values through `_enum()` so unknown values become `unknown` instead of raising.
- Every `translation_key` needs an entry in `strings.json` **and** `translations/en.json`.
- Realtime stats (`data.stats`) can be `None` — use `requires_stats=True` on descriptions that depend on it.

## Alert events
The coordinator diffs active (non-dismissed) alert uuids against `_alert_baseline` and fires `EVENT_ALERT` (`truenas_lightweight_alert`, `action: raised|cleared`). The baseline is `None` on the first refresh and after `alert.list` is denied (its `_optional` default is `None`, not `[]`); while `None` it is only re-seeded, never diffed, so neither startup, a denial nor regained access replays existing alerts. The event payload is public API for users' automations — only add fields.

## Config flow
User step validates with `subscribe_realtime=False`, splits host/URL input via `_split_host` (a port in a pasted URL overrides the Port field; IPv6 stored without brackets — the client adds them), unique id = host_id. Reauth replaces only the API key and aborts with `wrong_device` if host_id differs. Options change reloads the entry.

## Diagnostics
`diagnostics.py` redacts api_key, host, hostname, title, unique_id, remotehost, path. Redact any new identifying field you add to models.

`brand/icon.png` (256×256) and `icon@2x.png` (512×512) are required by HACS validation — a generic NAS icon; never use the TrueNAS/iXsystems logo.

This folder is shipped to users by HACS; keep it free of dev-only files other than this note.
