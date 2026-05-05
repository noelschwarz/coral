# Coral — Threat model (stub)

This document mirrors the structure of **§6 — Security threat model** in [`coral-engineering-spec.md`](./coral-engineering-spec.md). Fill in details as implementation hardens; the spec remains authoritative until this file is explicitly promoted.

## 6.1 Assets to protect

1. Captured session state (cookies, storage, etc.)
2. Vault encryption key (passphrase-derived; must not touch disk in plaintext)
3. Daemon API bearer tokens
4. Audit log (local sensitivity / privacy)

## 6.2 Threats and mitigations

| # | Threat | Mitigation (implementation notes) |
|---|--------|-----------------------------------|
| T1 | Malicious local process reads vault file | _TODO: SQLCipher + Argon2id key handling_ |
| T2 | Impostor binds to daemon port | _TODO: 127.0.0.1 bind + handshake/token auth_ |
| T3 | Non-operator browser completes handshake | _TODO: operator-mediated challenge_ |
| T4 | Compromised extension exfiltrates sessions | _TODO: least privilege + user education_ |
| T5 | Agent exceeds policy | _TODO: daemon-side enforcement via Playwright routes_ |
| T6 | Malicious agent with CDP control exfiltrates | **_Accepted risk / agent trust boundary — document clearly_** |
| T7 | Network adversary on localhost | _Out of scope per spec_ |
| T8 | Orphan Chromium after crash | _TODO: graceful shutdown + recovery sweeps_ |
| T9 | Offline vault theft | _TODO: Argon2 tuning + passphrase policy_ |
| T10 | Stale session replay | _TODO: document limitation + kill-on-login policies_ |
| T11 | Indirect prompt injection | _Out of scope v1; document_ |

## 6.3 Cryptographic primitives

_TODO: confirm runtime versions and exact parameters (Argon2id, SQLCipher settings, token sizes)._

## 6.4 Known limitations of v1

Keep aligned with spec §6.4 (IndexedDB best-effort, service worker limitations, malicious-agent trust model, etc.).
