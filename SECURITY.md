# Security policy

## Supported versions

Coral is pre-1.0. Until v1.0 ships, **only the latest `0.x.y` release receives
security fixes.** After v1.0, we'll maintain the most recent minor line.

| Version | Status |
|---|---|
| `0.5.x` | Supported (current) |
| `0.4.x` and earlier | Not supported — upgrade. |

## Reporting a vulnerability

**Please don't open a public issue for security-sensitive reports.** Use
GitHub's [Report a vulnerability][gh-report] flow from the Security tab of
this repository. The advisory channel is private and goes directly to the
maintainers. We'll acknowledge within **3 business days** and aim for a fix
or mitigation timeline within **10 business days** of acknowledgment.

[gh-report]: https://github.com/noelschwarz/coral/security/advisories/new

GitHub private vulnerability reporting is the only supported channel
pre-1.0. We may add a dedicated security email at v1.0 once we have a
stable contact alias in place.

When reporting, please include:

- A clear description of the issue and the impact.
- Steps to reproduce, ideally with a minimal proof-of-concept.
- The Coral version (`coral --version`), Python version, and OS.
- Your suggested fix or mitigation, if you have one.

We do not pay bounties in v1.x. We do credit reporters in the advisory and
in the release notes, with permission.

## Scope

In scope:

- **The Python daemon** (`coral/` in this repository) — vault, crypto, HTTP
  API, MCP server, policy engine, audit log.
- **The Chrome extension** (`extension/`) — manifest permissions, token
  handling, CORS posture, capture flow.
- **The packaged `coralbridge` distribution** on PyPI (once published).
- **The bundled behavior packs** (`coral/behavior_packs/*.yaml`).

Out of scope:

- **The user's local machine.** Coral runs locally; an attacker with root
  or same-user access has already won. See `THREAT_MODEL.md` "Self-attack
  scenarios" appendix for the tiered analysis.
- **Third-party dependencies.** Report `playwright`, `sqlcipher3`,
  `cryptography`, `mcp`, FastAPI, etc. vulnerabilities to their respective
  upstream projects. We'll bump pins promptly when alerted.
- **Indirect prompt injection through page content** reaching an agent.
  Documented as out of scope in spec §6.2 T11 — that's the agent's problem.
- **Malicious agents with valid CDP access** to a session they've been
  granted. Documented accepted risk in spec §6.2 T6 — choose your agents.

## Threat model

The authoritative document is [`THREAT_MODEL.md`](THREAT_MODEL.md). It
walks T1 through T11 plus a "Self-attack scenarios" appendix that maps
attacker capability tiers (A through F) to what each can achieve.

## What we promise

- We will not silently fix security issues. Every fix gets a CVE-style
  advisory + a `CHANGELOG.md` entry.
- We will not retaliate against good-faith reporters.
- We will not deny issues just because they're inconvenient. If we
  disagree with a report, we'll explain why in the advisory.

## What we ask

- Don't probe production systems you don't own.
- Don't exfiltrate user data beyond what's needed to demonstrate the issue.
- Give us a reasonable disclosure window before going public.
