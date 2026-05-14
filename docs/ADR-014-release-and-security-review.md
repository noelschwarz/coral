# ADR-014: Release pipeline + security review process

## Status

Accepted — Track I (2026).

## Context

ADR-013 deferred the release workflow until "the Chrome extension lands
and the §13.1 success criterion is demoable." Track H landed the
extension; this ADR records what we built in Track I to close v1.0 gates
3 (external security review) and 4 (release workflow exists + has run).

Three open questions during Track I planning:

1. **Auto-publish to PyPI on tag, or gate behind manual approval?**
2. **Sign artifacts with sigstore + provenance, or hash-only for v0.5?**
3. **What does "external security review" actually mean for an OSS
   project pre-revenue?**

## Decisions

### 1. Manual gate on PyPI publish

`.github/workflows/release.yml` runs on every `v*` tag and:

- Builds sdist + wheel via `uv build`.
- Runs the full quality gates (`ruff`, `pyright`, `pytest`, including
  Playwright e2e).
- Computes SHA-256 sums and attaches them to the GitHub release draft.
- Drafts (not publishes) a GitHub release pre-populated with a
  checklist for the human.
- The PyPI publish step lives in a `pypi` GitHub Environment that
  **requires manual approval** in the Actions UI before it fires.

This is deliberate. Once a version is on PyPI, it's there forever — pip
treats yanked releases as "not satisfying ==X.Y.Z" but they remain
downloadable. The manual gate is cheap insurance against publishing a
bad build under time pressure.

A `workflow_dispatch` trigger with a `dry_run: true` default also lets
us exercise the build pipeline without ever cutting a tag.

### 2. SHA-256 sums now, sigstore later

For v0.5.0 we ship `dist/SHA256SUMS` alongside the sdist + wheel in the
GitHub release. That's enough for a security reviewer to verify "what's
on PyPI matches what's in git" by hand.

Sigstore + SLSA provenance is the right destination, but adding it
correctly requires:

- A configured trusted-publishing relationship between this repo and PyPI.
- Verifier guidance in `INSTALL.md` (`pip install --require-hashes` flow).
- A `cosign verify-blob` documented procedure.

That's its own track. v1.0 should ship with full sigstore; v0.5.0 ships
with hashes.

### 3. External security review = solo + advisory window

For an OSS pre-revenue project, an "external security review" means:

- **A security-minded outside contributor** (not the maintainer) reads
  the code per the checklist in `docs/security-review-prep.md`.
- **A 14-day advisory window** before v1.0 publish during which the
  reviewer can file private reports via GitHub Security Advisories.
- **No NDA, no payment.** Credit in the advisory and the release notes.

We are explicitly **not** budgeting a third-party paid pentest for v1.0.
It's the right thing to do eventually but premature for the current
audience size.

The reviewer's findings, the threat-model accuracy assessment, and any
zero-days they spot get folded into the v1.0 release in a final
"security review summary" ADR (ADR-015, future).

## Consequences

- **PyPI publish is one human click away** on every tagged release.
  Faster than the previous "no release workflow exists" state; safer
  than "auto-publish on tag."
- **The 4-gate bar for v1.0** (ADR-013) updates: gate 4 ("release
  workflow exists + has run") is now **half-met** — workflow exists,
  hasn't run because we don't want to publish 0.5.0 yet. The first run
  happens at v0.6.0 if we cut one, or directly at v1.0 with the
  extension publish.
- **Anyone reviewing the code** has `SECURITY.md` (where to report) +
  `docs/security-review-prep.md` (what to focus on) + `THREAT_MODEL.md`
  (what we claim to defend against). Together they form the reviewer's
  briefing packet.

## Accepted limitations

- **PyPI name not yet reserved.** Someone could squat `coralbridge`
  before our first publish. Action item to claim it preemptively (cut
  a v0.0.0 placeholder, see ADR-013).
- **No reproducible builds yet.** The wheel is built on whatever
  `ubuntu-latest` image GitHub Actions provides. Reproducibility is a
  v1.1 concern.
- **No security review actually completed yet.** ADR-015 will replace
  this paragraph with "review completed by X on date Y; findings folded
  into v1.0 as commits A, B, C."
- **No coordinated-disclosure timeline policy.** `SECURITY.md` promises
  a 10-business-day fix-or-mitigation window after acknowledgment, but
  doesn't say what happens if a reporter wants to disclose sooner. We'll
  negotiate per-case until a real pattern emerges.

## When to revisit

- When the first external reviewer engages — replace the "What we
  promise" section of `SECURITY.md` with the actually-applied timeline.
- When PyPI trusted publishing is configured — drop the `skip-existing:
  false` from the publish step and document the verification flow.
- When sigstore lands — ADR-015 for the artifact-signing approach.

## Action items (tracked in v1.0 milestone)

- [ ] Claim `coralbridge` on PyPI with a v0.0.0 placeholder.
- [ ] Configure PyPI trusted publishing for this repo + workflow.
- [ ] Identify the external reviewer; share `docs/security-review-prep.md`.
- [ ] After review: write ADR-015 with findings + mitigations.
- [ ] On v1.0 tag: human-approve the PyPI publish; verify SHA-256 sums
      against the GitHub release before merging the release-notes PR.
