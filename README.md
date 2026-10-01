# TrueNAS Lightweight for Home Assistant

A lightweight, **read-only** Home Assistant integration for TrueNAS SCALE / Community Edition.

- Talks to the current TrueNAS **JSON-RPC 2.0 WebSocket API** (`wss://<host>/api/current`) — no deprecated REST.
- Authenticates with an **API key of a read-only user**; it never calls a method that changes anything.
- No external Python dependencies, one persistent connection, one parallel poll per interval.

Requires **TrueNAS 25.04 or newer** and **Home Assistant 2025.2 or newer**.

## Entities

All entities belong to one device representing the TrueNAS host.

| Entity | Type | Notes |
|---|---|---|
| CPU usage | sensor (%) | from the `reporting.realtime` feed |
| CPU temperature | sensor (°C) | disabled by default; not available on every host |
| Memory usage / used / available | sensor (%, GiB) | |
| ZFS ARC size | sensor (GiB) | disabled by default |
| Load (1/5/15 min) | sensor | |
| Last boot | timestamp | diagnostic |
| Version | sensor | diagnostic |
| Active alerts | sensor (count) | `alerts` attribute lists level, class and message |
| Highest alert level | enum sensor | `ok`, `info` … `emergency` |
| Problem | binary sensor | on when any active alert is WARNING or worse |
| Apps running | sensor (count) | |
| Pool *name* status | enum sensor | `online`, `degraded`, `faulted`, … |
| Pool *name* usage / free space | sensor (%, GiB) | |
| Pool *name* problem | binary sensor | on when ZFS reports the pool unhealthy |
| App *name* state | enum sensor | `running`, `deploying`, `stopping`, `stopped`, `crashed` |
| App *name* update | binary sensor | on when a newer catalog version is available |

New pools and apps are picked up automatically; removed ones become unavailable.

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

If the key is revoked, Home Assistant asks you to re-authenticate. If the user lacks permission for some calls (e.g. apps), those entities are skipped and a warning is logged instead of failing the whole integration.

## Development

```bash
scripts/setup      # install dev deps + pre-commit hooks
scripts/lint       # ruff format + lint
pytest             # unit tests (mocked TrueNAS + fake JSON-RPC server)
scripts/develop    # run Home Assistant on http://localhost:8123 with this integration
```

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
