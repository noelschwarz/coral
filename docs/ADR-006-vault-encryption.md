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

## Wheel / portability notes

CI and developers rely on published **`sqlcipher3`** wheels or local toolchain builds. If a platform lacks wheels or linking fails repeatedly, revisit ADR-006 and either pin `sqlcipher3-binary` (after verifying the CI matrix) or downgrade threat-model claims for metadata leakage (application-layer fallback).

## When to revisit

- Missing wheels or crashes on a Tier-1 platform for more than a short spike effort.
- Migration to a maintained async driver that officially supports SQLCipher connectors without per-thread execution.
