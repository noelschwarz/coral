# Coral Cloud — Threat model

Companion to [THREAT_MODEL.md](THREAT_MODEL.md), which covers the
**local-first daemon**. This document covers **Coral Cloud**, the
fully-hosted SaaS introduced in [ADR-019](docs/ADR-019-cloud-architecture.md).
The two products have *different* threat models — do not read one as a
delta of the other.

> **Status: design-stage.** No cloud code ships yet. This file is written
> alongside the architecture so the security posture is committed before
> implementation, not retrofitted after. Statuses below are **Planned**
> until the corresponding server PR lands.

## The one sentence that changes everything

The local product promises *your credentials never leave your machine*.
**Coral Cloud breaks that promise on purpose.** The hosted browser must
use your cookies to drive the target site, so Coral holds session state
it can decrypt at runtime. There is **no zero-knowledge mode** in the
hosted model — it is impossible by construction (ADR-019 §Context). If
you need credentials-never-leave-my-machine, use the local daemon. Cloud
trades that guarantee for zero-install convenience, and defends the
custodial state operationally rather than cryptographically.

## C.1 Assets to protect

1. **Captured session state** (cookies, storage) — now for *many* users,
   in one multi-tenant store. The crown jewels.
2. **Tenant data-encryption keys (DEKs)** and the **KMS customer master
   key (CMK)** that wraps them.
3. **API keys** (long-lived, per project) and **CDP session tokens**
   (short-lived, per open session).
4. **The CDP channel itself** — live control of an authenticated browser.
5. **Audit logs** — per tenant; integrity and confidentiality both matter.
6. **Control-plane / dashboard credentials** and operator/admin access.

## C.2 Threats and mitigations

Threats are renumbered `C1…` to avoid confusion with the local `T1…`.
Where a local threat has a cloud analogue it is noted.

| # | Threat | Mitigation (design intent) | Status |
|---|--------|----------------------------|--------|
| C1 | **Network adversary intercepts/redirects traffic** (was local T7, *out of scope*; now primary) | TLS 1.2+ on every hop (REST + `wss`). HSTS. No plaintext transport anywhere. CDP only over `wss`, never `ws`. | **Planned** |
| C2 | **Stolen API key** | Keys are high-entropy, prefixed (`ck_live_…`), stored only as SHA-256 hashes (reuse `crypto.py`), scoped per project, rotatable, revocable, rate-limited. Dashboard shows last-used. Compromise ⇒ revoke + rotate; blast radius bounded to one project's policies. | **Planned** |
| C3 | **CDP session-token theft / replay** | Tokens are single-tenant, single-session, short-TTL, bound to the open session's lifetime, and invalidated on close. A leaked token grants only that one browser until it expires/closes — never vault access, never another tenant. | **Planned** |
| C4 | **Cross-tenant data leakage** (vault, browsers, gateway, audit) | Tenant ID on every row + enforced at the query layer; per-tenant DEKs so even a query bug yields ciphertext for the wrong key; one browser per session with no shared context (ADR-010 carried forward); gateway refuses to route a token to a browser outside its tenant; audit partitioned by tenant. | **Planned** |
| C5 | **Cloud-provider / disk-level attacker reads the store** (was local T1) | Postgres at-rest encryption *plus* application-layer envelope encryption: `state_blob` = AES-256-GCM under a per-tenant DEK, DEK wrapped by a KMS CMK. Provider disk access yields double-encrypted blobs; the CMK lives in KMS, not in the DB. | **Planned** |
| C6 | **Malicious insider / compromised operator** | KMS decrypt is itself audited; every `state_blob` decrypt emits an audit event tied to a session-open or a login. Least-privilege IAM; no human path to bulk plaintext export; production access reviewed. This is the **hardest** threat and is honestly *mitigated, not eliminated* — custody implies insider risk. | **Planned (partial; never fully eliminable)** |
| C7 | **Browser-pod escape / sandbox break** | One disposable Chromium per session in a sandboxed container; seccomp/AppArmor; no persistent volume; destroyed on close. A pod escape is contained to a single session's already-decrypted state, not the fleet. | **Planned** |
| C8 | **Authenticated browser used for SSRF / exfiltration to attacker** | **Egress allowlist** on every browser pod: only the session origin + its policy `allowed_paths` are network-reachable. The same policy route handler that audits navigations (reused from `sessions.py`) blocks off-policy requests *before* the network call. Bounds what a malicious agent (C9) can reach. | **Planned** |
| C9 | **Malicious agent with valid CDP control exfiltrates** (was local T6, *accepted risk*) | Still partly an accepted risk — if you give an agent a logged-in browser, it can read what that login can read. Bounded by: per-session isolation, the policy engine (allow/deny/review per navigation), egress allowlist (C8), session TTL, and full audit. Worst case is scoped to one session's one origin. | **Planned (bounded, not eliminated)** |
| C10 | **Abuse of the platform** (hosted browsers + real auth = attractive for fraud/scraping/credential testing at scale) | Per-tenant rate limits and quotas; optional capturable-origin allowlist; anomaly detection on session volume; abuse reporting + suspension. A net-new threat with no local analogue. | **Planned** |
| C11 | **Phishing during remote login** (user tricked into logging into an attacker page in the live-view browser) | Show the real URL prominently in the live view; only persist state for the origin the user explicitly chose to connect; user education. The login is human-driven, so this is partly the user's responsibility — surfaced clearly. | **Planned** |
| C12 | **Stale-session replay** (was local T10) | Same posture as local: `kill_on_redirect_to_login` packs, policy-gated cookie write-back (ADR-018) re-encrypted into the vault, recapture-on-401 via re-login. Device-bound credentials remain out of scope for v1. | **Planned** |
| C13 | **Indirect prompt injection** (was local T11) | Out of scope for v1, same as local — the agent framework's problem. Documented. | **N/A (documented)** |
| C14 | **Account takeover of the Coral account itself** | Dashboard auth with MFA, session management, audit of admin actions. Compromise ⇒ attacker can open sessions / start logins for that tenant; bounded to that tenant, recoverable by revoke + rotate + re-login. | **Planned** |

## C.3 Self-attack scenarios

What an attacker gets at each capability tier (cloud edition):

| Tier | Capability | What they get |
|---|---|---|
| **A** | Internet only, no credentials | Nothing beyond the public REST surface (rate-limited, auth-required). The `wss` CDP endpoint rejects connections without a valid, unexpired, tenant-bound session token. |
| **B** | A leaked **CDP session token** | Control of **one** browser, for **one** session's origin, until it expires or closes. No vault access, no other sessions, no other tenants, no API surface. The narrowest credential by design (C3). |
| **C** | A leaked **API key** | Can list that project's sessions, open/close sessions, start logins, read that project's audit. Cannot read raw `state_blob` over the API (carried-forward discipline from local T4). **Equivalent to compromising that project's account** — revoke + rotate to recover (C2). |
| **D** | Read access to the **Postgres store** (e.g. backup leak) | Double-encrypted `state_blob`s. Useless without the per-tenant DEKs, which are wrapped by the KMS CMK that is not in the DB (C5). |
| **E** | Read access to store **and** KMS decrypt rights (deep compromise / insider) | Plaintext sessions. This is the worst case and the reason C6 is the hardest threat. Defenses are operational: least-privilege IAM, audited decrypts, no bulk-export path. **Honestly: a sufficiently privileged insider is a residual risk of any custodial service.** |
| **F** | Owns the **agent** driving a session | Equivalent to local Tier F, but network-bounded by the egress allowlist (C8): can exfiltrate what the session's origin exposes, nothing off-policy. Per-session isolation prevents cross-session reach. |

## C.4 Cryptographic & operational primitives

- **At rest:** AES-256-GCM on `state_blob` under a per-tenant DEK;
  DEK wrapped by a KMS-managed CMK (envelope encryption). Postgres
  at-rest encryption underneath as defense-in-depth. (Server ADR-0001.)
- **In transit:** TLS 1.2+ on REST and `wss`; CDP never over plaintext.
- **API keys:** generated/hashed/compared via the local product's
  `crypto.py` (`generate_token` / `hash_token` / `constant_time_compare`);
  stored as hashes only.
- **CDP session tokens:** short-TTL, single-session, tenant-bound;
  minted on `open_session`, invalidated on close/TTL. (Server ADR-0002.)
- **Audit invariant (carried forward):** every authenticated request,
  every auth failure, and **every `state_blob` decrypt** writes an audit
  row. Failure rows record the reason, never the secret.

## C.5 Known limitations of cloud v1

- **No zero-knowledge / E2E encryption** (C5/C6) — by construction.
- **Insider risk is mitigated, not eliminated** (C6).
- **Malicious-agent exfiltration within a session's origin** remains an
  accepted, bounded risk (C9), as in the local product.
- **Indirect prompt injection** out of scope (C13).
- **Device-bound session credentials** (DBSC/CHIPS) out of scope for v1
  (C12).
- **No external security review yet.** Same bar as the local product:
  GA must follow a third-party review of this model.
