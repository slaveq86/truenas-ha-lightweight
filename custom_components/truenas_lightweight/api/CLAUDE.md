# api/ – TrueNAS JSON-RPC client

Must stay **independent of Home Assistant** (no `homeassistant` imports) so it could be split into a PyPI library later.

## Protocol
- Endpoint `wss://{host}:{port}/api/current` (TrueNAS 25.04+). `ws_url=` constructor arg exists only for tests.
- JSON-RPC 2.0 requests `{"jsonrpc","id","method","params": [...]}`; params are always a positional list.
- Login: `auth.login_with_api_key [key]` → `true`/`false`.
- Datetimes arrive as `{"$date": epoch_ms}` — parse with `models._parse_date`.
- Errors: `error.code == -32601` (no `data`) = `TrueNASMethodNotFoundError` (method missing on this release); else `error.data.errname` → `ENOTAUTHENTICATED` = `TrueNASAuthError`, `EACCES` = `TrueNASPermissionError`, anything else `TrueNASError`. Transport failures/timeouts = `TrueNASConnectionError`.
- Push events: `core.subscribe ["reporting.realtime"]` and `["app.stats"]` (both every ~2 s); server sends `method: "collection_update"` with `params.fields` — a dict (cpu, memory, interfaces, aggregate disks, zfs) for realtime, a **list** of `{app_name, cpu_usage, memory, …}` for app.stats. Only compact samples (`RealtimeSample`, `AppStatsSample`) of each frame are kept, in bounded deques (`WINDOW_SIZE`), plus the newest full realtime frame for gauges; `realtime_stats()` / `app_stats()` consume them (rates averaged over the window) and return `None` — dropping the window — after 60 s without frames. Subscriptions (`_subscribe`, ids in `_subscriptions`) are best effort: e.g. app.stats fails while Docker is down; failures are warned once per streak. `notify_unsubscribed {collection}` (sent when an event source stops, and before the reply to our own `core.unsubscribe`) drops the id; `resubscribe(collection)` unsubscribes (`core.unsubscribe`, session-only, not a TrueNAS mutation) if needed and subscribes again.
- Updates: `update_info()` tries the method that worked last, then the other on `TrueNASMethodNotFoundError`: `update.status` (25.10+) or `update.check_available` (25.04; asks the iX update server — keep it on a slow cadence), so an upgrade needs no reconnect. `update.status` without a `status` dict (no check done yet) raises instead of meaning "up to date".

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
- `SystemInfo` DMI strings (manufacturer, product, serial) go through `_clean_dmi`, which drops vendor placeholders ("To Be Filled By O.E.M.", "Default string", …).
- `Stats.from_realtime(latest, window, previous_arc_hit_ratio)` must never raise on odd payloads (it runs in the coordinator): use `_dig`/`_number`/`_mean`. TrueNAS only reports disk I/O aggregated over all disks.
- When adding fields, add them to the fixtures in `tests/fixtures/` too.
