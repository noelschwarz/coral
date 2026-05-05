# Contributing to Coral

Thank you for helping build Coral. This document describes the **development workflow** for contributors.

## Principles

- **Spec-first:** [`coral-engineering-spec.md`](./coral-engineering-spec.md) is the source of truth for v1 scope and architecture.
- **Local-first / no hosted service:** do not add cloud sync, telemetry endpoints, or external session storage without an explicit spec change.
- **Safety:** passphrases, vault keys, bearer tokens, and captured session material must never be logged.

## Getting started

```bash
uv sync --all-extras
uv run coral --help
uv run pytest
```

**Chrome extension (dev):**

```bash
cd extension
npm ci
npm run build
```

Load unpacked from `extension/dist/` in Chrome.

## Before you open a PR

1. `uv run ruff check .` and `uv run ruff format .`
2. `uv run pyright`
3. `uv run pytest`

## Project conventions

- **Python:** 3.11+, async-first for daemon code, `pyright --strict` clean.
- **CLI:** `typer` entrypoint (`coral`).
- **Lint/format:** `ruff`.
- **Extension:** TypeScript, Manifest V3; keep builds reproducible with `npm run build`.

## Security

Review [`THREAT_MODEL.md`](./THREAT_MODEL.md) (stub mirroring spec §6 — expand as implementation lands). Report vulnerabilities through GitHub Security Advisories when enabled for the repo.
