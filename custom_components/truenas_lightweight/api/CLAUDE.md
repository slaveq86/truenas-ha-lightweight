# api/ – TrueNAS JSON-RPC client

Must stay **independent of Home Assistant** (no `homeassistant` imports) so it could be split into a PyPI library later.

## Protocol
- Endpoint `wss://{host}:{port}/api/current` (TrueNAS 25.04+). `ws_url=` constructor arg exists only for tests.
- JSON-RPC 2.0 requests `{"jsonrpc","id","method","params": [...]}`; params are always a positional list.
- Login: `auth.login_with_api_key [key]` → `true`/`false`.
- Datetimes arrive as `{"$date": epoch_ms}` — parse with `models._parse_date`.
- Errors: `error.data.errname` → `ENOTAUTHENTICATED` = `TrueNASAuthError`, `EACCES` = `TrueNASPermissionError`, anything else `TrueNASError`. Transport failures/timeouts = `TrueNASConnectionError`.
- Push events: `core.subscribe ["reporting.realtime"]`; server sends `method: "collection_update"` with `params.fields` (cpu/memory). Cached in the client, read via `realtime_stats()`, considered stale after 60 s.

## Client design (`client.py`)
- One persistent socket; `_read_loop` task resolves futures in `_pending` by id and fails all of them when the socket closes.
- `_handle` must never raise: type-check every field of server messages and drop malformed ones. `connected` also requires the reader to be alive, and `connect()` closes any leftover socket first, so a reader that dies anyway leads to a reconnect rather than calls timing out forever.
- `call()` auto-(re)connects; `connect()` is guarded by `_connect_lock` so concurrent calls share one login.
- Add new endpoints as typed helpers returning models, never raw dicts.

## Models (`models.py`)
- Dataclasses with `from_api()` classmethods; parse defensively with `.get()` — field names drift between TrueNAS releases. Only truly required keys (e.g. pool/app `name`) may use `[]`.
- Enum-like strings are upper-cased here; entities lower-case them.
- `Task` covers `rsynctask.query` (`from_rsync`) and `pool.snapshottask.query` (`from_snapshot`), keyed by `str(id)`. Last-run state comes from `job` (state/time_finished/error) if present, else the `state` dict (state/datetime/error, or `reason` for HOLD), else `PENDING`; snapshot states are normalised into `TASK_STATES` (`FINISHED`→`SUCCESS`, `ERROR`→`FAILED`). `Task.problem` = FAILED/ABORTED/HOLD. Snapshot task names include retention (`tank/photos (recursive, 2 weeks)`) because datasets usually have several tasks.
- `Disk` comes from `disk.query [[], {"extra": {"pools": true}}]` (without `extra.pools` the `pool` field is not filled) and is keyed by `Disk.key` = serial → identifier → name; disks sharing a key (bridges reporting one serial) get `{key}_{name}`. Never request `extra.passwords` (SED passwords). `disk_temperatures()` calls `disk.temperatures [[]]` → `{name: °C}`, dropping `null` (disks that can't report).
- When adding fields, add them to the fixtures in `tests/fixtures/` too.
