# ADR-013: Release strategy and the v0.5 → v1.0 path

## Status

Accepted — Track G (2026).

## Context

Track G's original scope included a release workflow (PyPI publish on tag,
GitHub release artifacts, SHA verification, signed tarballs). During the
track-planning conversation we decided to **defer all PyPI-publish tooling
until the Chrome extension is also ready**, and use Track G's bandwidth for
reliability + docs polish instead. This ADR captures that decision.

## Decisions

### 1. Distribution name: `coralbridge`

The `coral` name on PyPI is taken by an unrelated project. We pick
`coralbridge` for the distribution. The import name stays `coral` — users do
`pip install coralbridge` and then `import coral`. `pyproject.toml`,
`coral/__init__.py`, and the smoke test know about both names.

### 2. Versioning posture: 0.5.x now, 1.0.0 when the headline demo is real

We're at `v0.5.0` after Track G. The semantic bar for `1.0.0` is:

- The Chrome extension exists and ships in tandem.
- Spec §13's "agent reads my feed in under 5 minutes from clean install" is
  end-to-end demonstrable without a curl-paste-the-cookie workaround.
- An external security review has happened.
- PyPI + Chrome Web Store publish workflows exist and have run successfully
  at least once.

Until those four are true, we ship 0.5.x → 0.6.x → ... as feature work lands.
Patch releases (0.5.1, 0.5.2) are reserved for security fixes only.

### 3. No release workflow in Track G

We *could* ship a GitHub Actions workflow that, on a `v*` tag, builds an sdist
+ wheel, runs the test matrix, drafts a GitHub release, and (eventually)
uploads to PyPI. We don't, because:

- Without the extension, there's no audience to consume the package.
- Shipping a release pipeline early invites someone to push `v0.5.0` to PyPI
  as soon as it's green — at which point any name conflict or metadata
  mistake is on PyPI for as long as anyone has installed it. Better to
  publish once, deliberately, with the extension as the announcement.
- The work is small (~half a day) when we do it; not worth half-completing now.

The workflow design is sketched here for when we do build it:

```yaml
# .github/workflows/release.yml (future)
on:
  push:
    tags: ['v*']
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv build  # produces sdist + wheel
      - uses: actions/upload-artifact@v4
      - uses: pypa/gh-action-pypi-publish@release/v1  # gated on a manual approval
```

### 4. Cross-platform scope: macOS + Linux for CI, Windows manual

Track G adds macOS-latest to the CI matrix (alongside the existing
ubuntu-latest). Windows is out of CI for now because:

- SQLCipher Windows wheel availability is the open question from ADR-006.
  Track G's bandwidth doesn't include validating the AES-GCM fallback.
- Playwright Chromium on Windows in CI is slow and flaky enough that the
  CI cost > the signal.

When the extension lands and we're preparing the v1.0 publish, Windows
becomes a hard requirement — at that point we ship the AES-GCM fallback
flag (ADR-012 deferral #3) and add windows-latest to the matrix.

## Consequences

- Users today install via `git clone + uv sync`. README documents the path.
- `coralbridge` is reserved on PyPI by being the configured `[project] name`
  in `pyproject.toml`; we should claim the actual PyPI name before v1.0 to
  prevent squatting, even if we don't publish.
- The four-criterion gate for 1.0.0 forces the extension to land before
  launch — the right ordering, and consistent with spec §13.

## When to revisit

- When the Chrome extension reaches functional parity with the spec's §13.1
  flow.
- When someone reports squatting on `coralbridge` on PyPI (claim it
  preemptively).
- When Windows-target users start asking. Bundle that with ADR-012 deferral
  #3 (AES-GCM fallback).

## Action items

- [ ] Reserve `coralbridge` on PyPI (publish a 0.5.0 with just the README and
      no functional code, mark as Pre-release) — optional, do it before v1.0.
- [ ] When extension is functional: ship the release workflow above, write
      v1.0 launch ADR, run the security review.
- [ ] Update this ADR to `Resolved` once `v1.0.0` is on PyPI.
