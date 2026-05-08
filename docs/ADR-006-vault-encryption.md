# ADR-006: Vault encryption (SQLCipher integration)

## Status

Accepted — Week 1 Track A (2026).

## Context

Coral stores captured browser session state on disk (engineering spec §4.1 / §6.3). The threat model (§6.2 T1, T9) assumes **full-database encryption** at rest with a passphrase-derived key (Argon2id → SQLCipher raw key).

Alternatives considered:

1. **`sqlcipher3-binary`** — Prebuilt wheels and fewer compile failures on contributors’ machines. Wheel coverage varies by OS/arch; session scope prioritized shipping `sqlcipher3` because it already integrated in-repo.
2. **`pysqlcipher3`** — Effectively unmaintained; rejected per Week 1 Track A brief.
3. **Application-layer AES-GCM on plaintext SQLite** — Avoids native SQLCipher bindings but leaks schema metadata (table names, row counts, timestamps). Acceptable only if SQLCipher is genuinely unavailable; not chosen for v1.
4. **`aiosqlite` + custom connector** — Recent `aiosqlite` releases route everything through stdlib `sqlite3.connect`, which cannot substitute `sqlcipher3`. Rejected for SQLCipher builds; the vault uses a **single-thread `ThreadPoolExecutor`** so blocking SQLCipher calls never block the event loop from the caller’s perspective while staying asyncio-first at the API boundary.

## Decision

- Ship **`sqlcipher3`** (`sqlcipher3.dbapi2`) as the Python binding.
- Persist Argon2id parameters + salt in **`vault_meta.json`** next to `vault.db` (plaintext by necessity: required before any ciphertext page can be read). Mirror those fields into encrypted table **`vault_metadata`** for integrity checks after unlock.
- Apply schema via forward migrations under `coral/migrations/` tracked in **`schema_migrations`**.
- Serialize writes through an **`asyncio` queue + dedicated writer task** (spec §7.3); reads await the same single-worker executor so the SQLCipher connection is pinned to one OS thread.

## Wheel / portability matrix

Observed availability for `sqlcipher3==0.6.2` on PyPI at the time of Track A:

| Platform                     | Wheel published?                  | CI status |
|------------------------------|-----------------------------------|-----------|
| Linux x86_64 (manylinux)     | Yes (`sqlcipher3-binary`)         | Tier 1 — green in `ci.yml` |
| macOS arm64                  | Yes (`sqlcipher3-binary`)         | Tier 1 — verified locally  |
| macOS x86_64                 | Yes (`sqlcipher3-binary`)         | Tier 1 — verified locally  |
| Linux aarch64 (manylinux)    | Partial / version-dependent       | Tier 2 — falls back to source build via `libsqlcipher-dev`; CI installs the dev package |
| Windows x86_64               | Historically gappy                | Tier 3 — manual smoke only this session |

The repo currently depends on **`sqlcipher3`** (the source distribution; `sqlcipher3-binary` is a separate project that re-exports the same module). Linux CI installs `libsqlcipher-dev` to satisfy the source build (see `.github/workflows/ci.yml`). We accept the upstream-maintenance risk: if `sqlcipher3` stops publishing or its OpenSSL linkage breaks, we revisit (see "When to revisit").

## Accepted risks

- **Plaintext `vault_meta.json`** next to `vault.db` leaks the salt and Argon2id parameters. This is unavoidable: the parameters must be readable before the key is derived. The salt is not a secret; the threat model in §6.2 T1/T9 is unchanged.
- **Upstream wheel availability for `sqlcipher3`.** If wheels disappear for a Tier-1 platform, we either pin `sqlcipher3-binary` (after re-verifying its matrix) or fall back to application-layer AES-GCM and downgrade T1/T9 metadata claims in `THREAT_MODEL.md`. Decision deferred until that happens.

## When to revisit

- Missing wheels or crashes on a Tier-1 platform for more than a short spike effort.
- Migration to a maintained async driver that officially supports SQLCipher connectors without per-thread execution.
- Windows promoted to Tier 1 in CI (currently smoke-only).
