# tests/

Run with `pytest` (Python 3.13, `pytest-homeassistant-custom-component` pinned in `requirements_dev.txt`; it pins the HA version too).

## Fixtures (`conftest.py`)
- `mock_client` patches `TrueNASClient` in both `__init__` and `config_flow` with an autospec mock fed from `fixtures/*.json`. Change behaviour per test via `mock_client.<method>.return_value` / `.side_effect`.
- `mock_config_entry` – entry with `unique_id=HOST_ID` and `ENTRY_DATA`.
- `fixtures/*.json` mirror real TrueNAS 25.04 responses; `reporting_realtime.json` is the `fields` of a `reporting.realtime` event. Keep them realistic — prefer capturing real payloads over inventing fields.

## Files
- `test_client.py` – real client against `FakeTrueNAS`, an in-process aiohttp JSON-RPC server on 127.0.0.1 (needs the `socket_enabled` fixture). Extend `FakeTrueNAS.responses` for new methods.
- `test_config_flow.py` – user/reauth/options flows; `async_setup_entry` is patched out.
- `test_init.py` – setup, entity states, dynamic pools/apps, coordinator error paths, diagnostics.

## Gotchas
- After `async_fire_time_changed`, use `hass.async_block_till_done(wait_background_tasks=True)` — coordinator refreshes run as background tasks (see `_tick`).
- Entity ids derive from the device name `truenas` + translated names, e.g. `sensor.truenas_pool_tank_status`.
