# Contributing to Coral

Thanks for considering it. Coral is OSS-first and external contributors are
welcomed.

## Set up a dev environment

```bash
git clone https://github.com/noelschwarz/coral
cd coral
uv sync --all-extras
uv run playwright install chromium    # ~150 MB; required for integration + e2e tests
```

Python 3.11+ is required. On Linux you need `libsqlcipher-dev` system-installed
(`sudo apt-get install -y libsqlcipher-dev`). macOS pulls SQLCipher in via
`brew install sqlcipher`.

## Run the suite

```bash
uv run pytest                                   # all 168+ tests
uv run pytest tests/unit                        # unit only (~5s)
uv run pytest tests/integration tests/e2e       # subprocess + real Chromium
uv run pytest --cov=coral --cov-report=term     # with coverage
```

The full suite runs in ~40 s locally on a recent laptop.

## Quality gates (also enforced by CI)

```bash
uv run ruff check coral tests
uv run ruff format --check coral tests
uv run pyright                                  # strict mode
```

All three must be clean before a PR merges.

## Coding conventions

- **Audit log discipline.** Every authenticated path writes an `audit_log` row.
  Failure paths record the *reason* only — never the token, challenge, or any
  payload that's reversible to a credential. See `coral/audit.py` for the
  canonical write path; all audit insertion flows through it.
- **Async throughout.** FastAPI handlers are `async def`. Vault calls are
  awaited. Avoid blocking I/O in handler bodies.
- **`127.0.0.1` is non-negotiable.** The HTTP API and MCP HTTP transport bind to
  loopback only. There is no configuration path that changes this. Spec §6.2 T2.
- **Type-strict.** `pyright` runs in `strict` mode. New code must pass without
  `# type: ignore` unless there's a documented reason (decorator-typing
  limitations, third-party API shape gaps).
- **Test new code.** Property-based tests welcome where they fit
  (`tests/unit/test_policy.py` uses Hypothesis).

## ADRs

Architecture Decision Records live in [`docs/`](docs/) (`ADR-006` through
`ADR-013` at time of writing). Anything that changes a non-trivial tradeoff or
contradicts the spec needs an ADR. Format: short, honest about alternatives
considered, includes a "When to revisit" section.

## PR workflow

1. Branch off `main`.
2. Implement, write tests, run the quality gates locally.
3. Open a PR with a description that covers: what changed, why, what
   alternatives you considered, and a self-attack section if the change touches
   security-critical paths.
4. CI runs Linux + macOS jobs. Both must be green.
5. Maintainer reviews; once approved, squash-merge.

## What's in scope vs. out of scope

In scope for this repo:
- The Python daemon (`coral/`)
- The CLI (`coral/cli.py`)
- The vault, crypto, policy engine, MCP scaffold
- Behavior packs (`coral/behavior_packs/`)
- Tests, docs, ADRs

Out of scope (separate codebases / future work):
- The Chrome extension (`/extension/`, mostly skeleton today)
- Cross-platform installer packages
- A hosted/relay component (never, per spec §2.1)

## Threat-model contributions

The [`THREAT_MODEL.md`](THREAT_MODEL.md) is load-bearing. Changes that affect
T1-T11 status, or that introduce a new threat vector, must update the doc in
the same PR.

## Reporting security issues

For security-sensitive issues, use GitHub's "Report a vulnerability" flow on
the Security tab instead of opening a public issue. Coral is pre-1.0 and has
not yet had an external security review.

## License

MIT. By contributing you agree your contribution is licensed under the same.
