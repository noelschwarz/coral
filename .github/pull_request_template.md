<!--
Thanks for the contribution. Fill out the sections below. Anything marked
[required] blocks review.
-->

## Summary [required]

<!-- One-paragraph "what changed and why". Not a commit log. -->

## Motivation

<!-- The user-visible problem this solves. Link the issue if there is one. -->

Closes #

## Approach

<!-- Brief description of the implementation. Alternatives considered and why this one wins. Link any ADR added/updated in this PR. -->

## Test plan [required]

<!-- How you verified the change. Reviewers will treat anything not listed as untested. -->

- [ ] Unit tests added/updated
- [ ] Integration / e2e covered (if behavior crosses process boundaries)
- [ ] Manually exercised on macOS
- [ ] Manually exercised on Linux
- [ ] `uv run ruff check coral tests` clean
- [ ] `uv run ruff format --check coral tests` clean
- [ ] `uv run pyright` clean
- [ ] `uv run pytest` green locally

## Threat-model impact

<!--
Does this change Coral's trust boundaries, attack surface, or any of the
T1-T11 threats in THREAT_MODEL.md? If yes, describe the impact and update
THREAT_MODEL.md in this PR. If no, say "None" — but think about it.
-->

## Breaking changes

<!-- API, CLI, config-file, or behavior changes that existing users will notice. None if not applicable. -->

- [ ] This PR introduces a breaking change.

## DCO

<!--
Every commit must be signed off (`git commit -s`). See CONTRIBUTING.md.
If your history is missing sign-offs, fix it with:
    git rebase --signoff main && git push --force-with-lease
-->

- [ ] All commits in this PR are signed off.
