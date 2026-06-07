# ADR-019: Coral Cloud architecture (Track CLOUD)

## Status

Proposed — Track CLOUD, PR 0.1 (2026-06).

This ADR opens a new track. Where ADR-006 … ADR-018 describe the
**local-first daemon** that ships from this repository, ADR-019 and the
`CLOUD-*` docs describe a second, **fully-hosted** product — *Coral
Cloud* — built and operated as a SaaS. The local daemon is not being
retired; Cloud is an additional deployment shape for users who don't
want to install anything.

## Context

The local daemon's entire value proposition rests on one sentence from
the README: **"The agent never sees your password,"** backed by a
stronger, unstated one — *your credentials never leave your machine*.
[THREAT_MODEL.md](../THREAT_MODEL.md) is built around that: the vault is
a passphrase-encrypted SQLCipher file on the user's disk, the daemon
binds `127.0.0.1` only, and the CDP URL handed to the agent is
`ws://127.0.0.1:<port>`. A network adversary is explicitly out of scope
because nothing is ever on the network.

That posture is also the adoption ceiling. To use Coral today a user
must: install a SQLCipher native library, `pip install coralbridge`,
`playwright install chromium`, run a daemon, load an unpacked Chrome
extension, pair it over a clipboard challenge, and capture a session.
Six install steps and an OS-specific native dependency before the first
`coral_open_session`. For the dominant requested use case — *"drop Coral
into my agent code with an API key and go"* — that friction is fatal.

The maintainer's decision (recorded in the planning thread, 2026-06) is
to offer a **second** product with the friction removed:

- **Browser execution and session storage move into Coral's cloud.**
  The agent receives a remote CDP URL (`wss://…`) instead of a local
  one.
- **Capture becomes remote interactive login.** Instead of an
  extension scraping the user's real Chrome, the user logs into the
  target site inside a *Coral-hosted* browser (live-view), and the
  resulting authenticated state is persisted in the cloud vault.
- **Coral operates it as a SaaS.** Accounts, API keys, multi-tenancy,
  metering, billing.
- **The first client SDK is Python.**

The unavoidable consequence — and the reason this needs an ADR rather
than a feature flag — is that **Coral becomes the custodian of users'
live authenticated sessions.** The local product's central promise is
deliberately broken. We must be explicit about exactly how, and rebuild
the threat model from scratch ([THREAT_MODEL-cloud.md](../THREAT_MODEL-cloud.md)).

### The promise that cannot be kept, and why

A natural instinct is to preserve trust with end-to-end / zero-knowledge
encryption: encrypt the session under a key only the user holds, so
Coral stores ciphertext it cannot read. **This is impossible in the
hosted-execution model and we should stop anyone who proposes it.** The
cloud browser must *use* the cookies — inject them into a live Chromium
and drive the target site — which requires the plaintext at runtime,
inside Coral's infrastructure. A system that can decrypt to drive the
browser is not zero-knowledge by definition. The honest posture is
custodial encryption with the smallest possible plaintext window
(ADR-0001 in the server repo), not a zero-knowledge claim we'd have to
walk back.

Users who need the credentials-never-leave-my-machine guarantee should
keep using the **local daemon**. Cloud is a different point on the
convenience/trust curve, and the docs must say so plainly.

## Decision

### 1. Two products, one protocol, two repositories

```
┌──────────────────────────────┐        ┌─────────────────────────────────┐
│  OSS repo (this one)          │        │  coral-cloud-server (PRIVATE)   │
│  Apache-2.0                   │        │  proprietary                    │
│                               │        │                                 │
│  • local daemon + CLI         │        │  • control plane (FastAPI)      │
│  • Chrome extension           │        │  • cloud vault (Postgres + KMS) │
│  • coral-cloud Python SDK ────┼──REST──┤  • execution plane (browsers)   │
│  • protocol/openapi.yaml  ◀───┼─spec───┤  • CDP gateway (wss relay)      │
│  • ADR-019, THREAT_MODEL-     │        │  • remote-login live view       │
│    cloud.md (transparency)    │        │  • dashboard, billing           │
└──────────────────────────────┘        └─────────────────────────────────┘
```

We follow the **Browser-Use model** of staying open-source: the
*library and client SDKs* are public; the *hosted backend* is a private
repository. (Firecrawl's alternative — a single AGPL monorepo with
cloud-only features gated functionally — was considered and rejected for
now because Coral runs the SaaS itself and does not need copyleft to
deter a rival host; the server simply isn't published.)

What lives where:

- **OSS repo (this one):** the `coral-cloud` **Python SDK**, the
  **wire-protocol spec** (`protocol/openapi.yaml`), this ADR, and the
  **cloud threat model**. The protocol and threat model are public *on
  purpose* — transparency about what we hold and how is consistent with
  the project's existing "publish the threat model" ethos, and the SDK
  is what users read.
- **Private repo (`coral-cloud-server`):** everything that touches
  customer sessions or infrastructure — control plane, vault internals,
  execution plane, gateway, dashboard, billing. Its ADRs (encryption
  internals, gateway token mechanics, infra stack) live there because
  they describe operational systems, not the public contract.

### 2. Component model

The hosted system is five planes plus a dashboard. Each is detailed in a
server-repo ADR; this ADR only fixes the boundaries and the data flow.

| Plane | Responsibility | Reuses from OSS |
|---|---|---|
| **Control plane** | Multi-tenant FastAPI: API-key auth, org/project model, session/policy/audit/review endpoints, metering hooks. | `http_api.py` shapes, `api_models.py`, `auth.py` patterns, `audit.py` discipline |
| **Cloud vault** | Postgres store of sessions/policies/audit/reviews, per-tenant. `state_blob` encrypted with envelope encryption (KMS CMK → per-tenant DEK → AES-256-GCM). | `models.py`, migration discipline, `crypto.py` |
| **Execution plane** | One isolated headless Chromium per open session: restore state, install the policy route handler, auto-close on TTL. Runs in a sandboxed container with an egress allowlist. | `sessions.py` `SessionServer`, `restoration.py`, `policy.py`, behavior packs |
| **CDP gateway** | `wss` relay: validates a short-lived per-session token, proxies CDP frames to the right browser, enforces tenant isolation. | *new* |
| **Remote login** | Hosted live-view browser the user drives to log in; captures the resulting state into the vault. | `restoration.py` (inverted: capture, not restore) |
| **Dashboard** | Accounts, API keys, the login flow UI, session list, policy editor, audit/review surfaces, billing. | *new* |

### 3. End-to-end data flow

```
 (a) USER logs in            (b) capture            (c) AGENT opens          (d) AGENT drives
 ┌──────────────┐            ┌──────────┐           ┌───────────────┐        ┌──────────────┐
 │ dashboard    │  live view │ exec     │  encrypt  │ coral-cloud   │ wss    │ CDP gateway  │
 │ "Connect     │───────────▶│ browser  │──────────▶│ SDK           │───────▶│ → exec       │
 │  GitHub"     │  (noVNC/   │ (login)  │  → vault  │ open_session()│  token │   browser    │
 └──────────────┘ screencast)└──────────┘           └───────────────┘        └──────────────┘
        │                                                    │                      │
        └─ 2FA / captcha handled by the human, in-browser    │   policy + audit on every nav (reused engine)
                                                             ▼
                                              returns {handle, cdp_url=wss://…, expires_at}
```

1. **Login (a–b).** The user clicks *Connect &lt;site&gt;* in the
   dashboard. The execution plane launches a live-view browser; the user
   logs in (2FA, captcha, SSO — all interactive, all human). On "done,"
   the plane captures cookies + storage and the control plane writes an
   **encrypted** session row. The user's password is entered into a
   Coral-hosted browser and is never persisted — only the resulting
   session state is.
2. **Open (c).** The agent's code calls `coral.open_session(id,
   purpose=…)` with an API key. The control plane checks the key + tenant
   + policy, the execution plane spins up an isolated Chromium and
   restores the (decrypted) state, and the control plane mints a
   short-lived CDP session token. The SDK returns
   `{handle, cdp_url: "wss://gateway…/<token>", expires_at, policy_summary}`.
3. **Drive (d).** The agent connects any CDP client to the `wss` URL.
   The gateway authenticates the token and relays to that one browser.
   Every navigation still flows through the **same policy engine and
   audit path** as the local product. On close / TTL, the browser is
   destroyed and (policy-gated) cookie write-back re-encrypts deltas
   into the vault.

The contract at the SDK boundary is **identical in shape** to the local
MCP `coral_open_session` response — `cdp_url` is just `wss://` instead of
`ws://127.0.0.1`. This is deliberate: existing `examples/` (browser-use,
Stagehand) become drop-in by swapping where `cdp_url` comes from.

### 4. What we reuse vs. build new

The policy engine, restoration, audit discipline, session lifecycle, and
token crypto are **portable as-is or with light edits** — the local
product already solved the hard browser-session parts. The genuinely new
surface is everything that makes it *multi-tenant, hosted, and
network-exposed*: KMS encryption at rest, the CDP-over-internet gateway,
browser-fleet orchestration, remote login, and SaaS plumbing (keys,
dashboard, metering, billing). The risk concentrates in the gateway
(handing browser control to a remote client) and the vault (holding
plaintext-recoverable credentials) — both get dedicated server-repo ADRs
and threat-model entries.

## Threat model impact

This is not an incremental change to [THREAT_MODEL.md](../THREAT_MODEL.md);
it is a different threat model, written fresh in
[THREAT_MODEL-cloud.md](../THREAT_MODEL-cloud.md). The headline shifts:

- **Network adversary moves from "out of scope" to "primary."** The CDP
  URL is now reachable over the internet. TLS everywhere, per-session
  tokens, and gateway tenant-isolation become load-bearing.
- **"Disk-level attacker" becomes "cloud-provider / insider /
  cross-tenant attacker."** SQLCipher-on-your-disk is replaced by
  Postgres + KMS envelope encryption that *Coral can decrypt*. The
  defense is operational (key management, access controls, audit of
  every decrypt), not cryptographic-impossibility.
- **A whole new asset class: live multi-tenant credentials in one
  place.** A breach is no longer one user's laptop; it is the fleet.
  This raises the bar for everything — abuse controls, SOC2 trajectory,
  DPA, on-call.

The local product's guarantees are unchanged and remain the
recommendation for users who need them.

## Consequences

- **Adoption ceiling lifts.** `pip install coral-cloud`, set
  `CORAL_API_KEY`, call `open_session`. No native deps, no daemon, no
  extension.
- **Coral takes on custodial responsibility** for users' sessions, with
  all the compliance and operational weight that implies. This is a
  business decision as much as a technical one.
- **Two codebases to keep in step** via one versioned protocol. The
  `protocol/openapi.yaml` in this repo is the contract; server and SDK
  both conform to it.
- **The local daemon keeps its promise.** Nothing here weakens it; Cloud
  is additive.

## Accepted limitations

- **No zero-knowledge encryption** in the hosted model, by construction
  (§Context). Documented, not hidden.
- **Capture still requires a real human login** — Coral cannot
  manufacture a session from nothing. "Remote interactive login" removes
  the *local install*, not the login itself.
- **Latency and cost** of a remote browser fleet are real and unbounded
  in this ADR; the infra-substrate tradeoff is deferred to the server
  repo's infra-stack ADR.

## When to revisit

- **If a hardware-token / confidential-compute path makes a meaningfully
  stronger custody story viable** (e.g. decrypting only inside an
  enclave), revisit the "no zero-knowledge" stance.
- **If self-hosting demand is strong**, revisit the Browser-Use vs.
  Firecrawl repo decision — an AGPL `coral-cloud-server` is the pivot.
- **Before GA**, the cloud threat model must have had an external review,
  mirroring the bar the local product set for its own v1.0.
