# Coral — Threat model

Mirrors **§6 — Security threat model** in [`coral-engineering-spec.md`](./coral-engineering-spec.md). The spec remains authoritative; this file tracks **implementation status** for shipped code.

## 6.1 Assets to protect

1. Captured session state (cookies, storage, etc.)
2. Vault encryption key (passphrase-derived; must not touch disk in plaintext)
3. Daemon API bearer tokens
4. Audit log (local sensitivity / privacy)

## 6.2 Threats and mitigations

| # | Threat | Mitigation (implementation notes) | Status |
|---|--------|-------------------------------------|--------|
| T1 | Malicious local process reads vault file | SQLCipher full-DB encryption; Argon2id-derived key; ADR-006 | **Implemented** — ciphertext pages via SQLCipher; plaintext `vault_meta.json` holds salt + Argon2 parameters only (see ADR-006). |
| T2 | Impostor binds to daemon port | Local bind + future bearer-token middleware | **Partially implemented** — `127.0.0.1` bind + PID file; **HTTP bearer verification not enforced yet** (next milestone). |
| T3 | Non-operator browser completes handshake | Operator-mediated challenge printed on TTY | **Implemented for v1** — challenge printed at daemon start; not exposed over HTTP until `/auth/handshake` ships. |
| T4 | Compromised extension exfiltrates sessions | Least privilege + user education | Not yet implemented |
| T5 | Agent exceeds policy | Daemon-side enforcement via Playwright routes | Not yet implemented |
| T6 | Malicious agent with CDP control exfiltrates | **_Accepted risk / agent trust boundary — document clearly_** | Documented in spec |
| T7 | Network adversary on localhost | Out of scope per spec | N/A |
| T8 | Orphan Chromium after crash | Graceful shutdown + recovery sweeps | Not yet implemented |
| T9 | Offline vault theft | Argon2id tuning + passphrase policy | **Implemented** — minimum 12-character passphrase; production Argon2 parameters in `coral.crypto.PRODUCTION_PARAMS`; resilience depends on SQLCipher packaging (ADR-006). |
| T10 | Stale session replay | Document limitation + kill-on-login policies | Not yet implemented |
| T11 | Indirect prompt injection | Out of scope v1; document | N/A |

## 6.3 Cryptographic primitives

- **Argon2id:** `coral.crypto.PRODUCTION_PARAMS` (spec §6.3 alignment); **`coral.crypto.TEST_PARAMS`** for automated tests only.
- **SQLCipher:** `sqlcipher3` bindings; key formatted as raw hex `PRAGMA key` (see `format_sqlcipher_hex_pragma_key`).
- **API tokens:** `generate_token` / `hash_token` / `constant_time_compare` in `coral.crypto`.

## 6.4 Known limitations of v1

Aligned with spec §6.4 (IndexedDB best-effort, malicious-agent trust model, etc.).
