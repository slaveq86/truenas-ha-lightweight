# Integration package

## Data flow
`__init__.py` builds a `TrueNASClient` (HA session, `verify_ssl` from entry) → `TrueNASCoordinator` (`coordinator.py`) polls every `scan_interval` (options, default 30 s) with one `asyncio.gather` → `TrueNASData` (`api/models.py`) → entities read `coordinator.data`. `entry.runtime_data` is the coordinator; `TrueNASConfigEntry` type alias lives in `coordinator.py` to avoid circular imports.

## Error mapping (coordinator)
- `TrueNASAuthError` → `ConfigEntryAuthFailed` (starts reauth flow).
- `TrueNASError` → `UpdateFailed`.
- `TrueNASPermissionError` on optional sources (alerts/pools/apps) → logged once, empty default; only `system.info` is mandatory. Wrap new optional sources in `_optional()`.

## Entities
- All entities sit on one device keyed by the entry `unique_id` (= `system.host_id`). Unique ids: `{host_id}_{key}`, `{host_id}_pool_{name}_{key}`, `{host_id}_app_{name}_{key}` — never change these formats (breaks users' entity registry).
- Sensors are declared as `EntityDescription` tables in `sensor.py` (`value_fn` per description). Add new sensors there, not as new classes.
- Pools/apps are added dynamically via `entity.async_track_items`; per-item entities must override `available` to go unavailable when the item disappears.
- Enum sensors: options are lowercase; map API values through `_enum()` so unknown values become `unknown` instead of raising.
- Every `translation_key` needs an entry in `strings.json` **and** `translations/en.json`.
- Realtime stats (`data.stats`) can be `None` — use `requires_stats=True` on descriptions that depend on it.

## Config flow
User step validates with `subscribe_realtime=False`, splits host/URL input via `_split_host` (a port in a pasted URL overrides the Port field; IPv6 stored without brackets — the client adds them), unique id = host_id. Reauth replaces only the API key and aborts with `wrong_device` if host_id differs. Options change reloads the entry.

## Diagnostics
`diagnostics.py` redacts api_key, host, hostname, title, unique_id. Redact any new identifying field you add to models.

`brand/icon.png` (256×256) and `icon@2x.png` (512×512) are required by HACS validation — a generic NAS icon; never use the TrueNAS/iXsystems logo.

This folder is shipped to users by HACS; keep it free of dev-only files other than this note.
