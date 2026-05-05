# Coral

Coral is an open-source, **local-first** session bridge that lets AI agents borrow a user’s already-authenticated browser sessions on a per-site, per-action, fully audited basis—without ever seeing the user’s password. See the engineering specification for scope, architecture, and security:

- [`coral-engineering-spec.md`](./coral-engineering-spec.md)

This repository is in **active development** toward v1. The instructions below are for **contributors** (development workflow), not end-user installation.

## Development quickstart

**Prerequisites**

- Python **3.11+**
- [`uv`](https://docs.astral.sh/uv/) (recommended) or another PEP 621–aware installer
- **SQLCipher** available to the `sqlcipher3` Python binding (on macOS, `brew install sqlcipher` if wheels fail)
- **Node.js 20+** (for the Chrome extension)

**Python package**

```bash
git clone <coral-repo-url>
cd coral
uv sync --all-extras
# Alternative: pip install -e ".[dev]"
uv run coral --help
```

**Tests, lint, and type-check**

```bash
uv run ruff check coral tests
uv run ruff format --check coral tests
uv run pyright
uv run pytest
```

**Chrome extension**

```bash
cd extension
npm install
npm run build
```

In Chrome: **Extensions → Load unpacked →** select `extension/dist/`.

## Repository layout

See **§10** of [`coral-engineering-spec.md`](./coral-engineering-spec.md).

## License

MIT — see [`LICENSE`](./LICENSE).
