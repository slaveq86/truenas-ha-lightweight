# truenas-ha-lightweight

Read-only Home Assistant custom integration (domain `truenas_lightweight`) for TrueNAS 25.04+, using the JSON-RPC 2.0 WebSocket API with an API key of a `READONLY_ADMIN` user. Distributed via HACS.

## Layout
- `custom_components/truenas_lightweight/` – the integration (see its CLAUDE.md)
  - `api/` – HA-agnostic WebSocket client + models (see its CLAUDE.md)
- `tests/` – pytest-homeassistant-custom-component tests (see its CLAUDE.md)
- `scripts/` – `setup`, `lint`, `develop` (runs HA on :8123 with `config/`), `release.py` (used by CI)
- `.github/workflows/` – `validate.yml` (hassfest + HACS), `tests.yml` (ruff + pytest, PRs and reused by release), `release.yml`

## Commands
```bash
scripts/setup          # pip install -r requirements_dev.txt + pre-commit install
ruff check . && ruff format --check .
pytest                 # asyncio_mode=auto, Python 3.13
scripts/develop        # local HA instance for manual testing
```

## Hard rules
- **Read-only**: only call query/info/list methods, `update.status` / `update.check_available` (SYSTEM_UPDATE_READ) and `core.subscribe` / `core.unsubscribe` (session-only). Never add a method that mutates TrueNAS state (start/stop apps, dismiss alerts, scrubs, updates…) — the key's role would reject it anyway and it breaks the integration's promise.
- **wss:// only**: TrueNAS revokes API keys sent over plain ws. Never add an http/ws option.
- **No runtime dependencies**: `manifest.json` `requirements` stays empty; use aiohttp from HA.
- `hacs.json` accepts only HACS's documented keys (`name`, `homeassistant`, `country`, `hacs`, `zip_release`, `filename`, `content_in_root`, `hide_default_branch`, `persistent_directory`) — anything else (e.g. old `render_readme`) fails validation.
- Minimum Home Assistant: 2025.2 (`hacs.json`). Don't use newer HA APIs without bumping it.
- Line length 120; ruff config in `pyproject.toml`. Keep `strings.json` and `translations/en.json` identical.
- **Never bump versions by hand.** `.github/workflows/release.yml` runs on every push to `main`: tests → `scripts/release.py` bumps `manifest.json` + `pyproject.toml`, moves `## [Unreleased]` in `CHANGELOG.md` under the new version, commits `chore(release): vX.Y.Z`, tags and creates a GitHub release. `main` is protected by a ruleset (PR + required checks `test`, `hassfest`, `hacs`); the release commit bypasses it by pushing with the `RELEASE_DEPLOY_KEY` deploy key (secret), atomically with its tag.
- Every user-visible change adds a line under `## [Unreleased]` in `CHANGELOG.md` (Keep a Changelog sections: Added/Changed/Fixed/Removed).
- Bump level comes from commit messages since the last tag: `#major`, `type!:` or `BREAKING CHANGE` → major; `feat:` or `#minor` → minor; otherwise patch.
