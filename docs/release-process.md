# Release process

Concrete, copy-pasteable steps for cutting a Coral release. The release
workflow ([`.github/workflows/release.yml`](../.github/workflows/release.yml))
does the actual work — this doc just walks you through what to do
before, during, and after pushing the tag.

## One-time setup

These only need doing once, ever, before the first PyPI publish.

### 1. Configure PyPI trusted publishing

PyPI uses GitHub's OIDC to verify that a release was built from this
repo's workflow — no API tokens stored anywhere.

1. Sign in at https://pypi.org and go to "Your projects" → "Publishing".
2. Add a **pending publisher** with:
   - PyPI project name: `coralbridge`
   - Owner: `noelschwarz`
   - Repository name: `coral`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
3. The first successful publish promotes the pending entry to a real
   one and registers the project on PyPI.

### 2. Configure the `pypi` GitHub Environment

This is the manual gate that prevents an accidental publish.

1. In GitHub → repo Settings → Environments → New environment, name it
   `pypi`.
2. Add yourself as a **required reviewer**.
3. (Optional) Restrict to the `main` branch under "Deployment branches."

After this, every release tag will pause at the `publish-pypi` job and
wait for you to click "Approve and deploy" in the Actions UI.

### 3. Sign your tags (optional but recommended)

```sh
git config --global user.signingkey <your-gpg-or-ssh-key>
git config --global commit.gpgsign true
git config --global tag.gpgsign true
```

PyPI doesn't check signatures, but signed tags are useful for the
GitHub Release page and for anyone who clones the repo later.

## Per-release checklist

Worked example: cutting `v0.6.0` from `main`.

### 1. Pre-flight

```sh
git checkout main
git pull --ff-only origin main
git status                       # clean working tree
uv run pytest                    # full suite green
uv run ruff check coral tests
uv run ruff format --check coral tests
uv run pyright
uv build                         # sdist + wheel build cleanly
```

Sanity-check that the version in `pyproject.toml` matches the tag
you're about to push:

```sh
grep '^version = ' pyproject.toml   # should be the target version
```

Update `CHANGELOG.md` to add the release date and ensure the
`[Unreleased]` section is empty (or moved into the new release
entry). Commit any final-polish edits before tagging.

### 2. Build the Chrome extension bundle

The release workflow doesn't build the extension (it's a separate
codebase under `extension/`). Build it locally and stash the zip
somewhere; you'll attach it to the GitHub Release in step 4.

```sh
cd extension
npm ci
npm run build
cd dist
zip -r ../../coral-extension.zip .
cd ../..
```

The file `coral-extension.zip` at the repo root is what you'll upload.
Keep the name stable (no version number) so the README can link to
`releases/latest/download/coral-extension.zip`.

### 3. Tag and push

```sh
git tag -s v0.6.0 -m "coralbridge 0.6.0"
git push origin v0.6.0
```

The release workflow now runs:

1. **`build`** — installs SQLCipher, syncs uv, runs the full quality
   gates, builds sdist + wheel, computes `SHA256SUMS`.
2. **`release`** — drafts a GitHub Release with the changelog stub
   and attaches the build artifacts.
3. **`publish-pypi`** — paused on the `pypi` environment gate, waiting
   for your approval.

Watch it at `https://github.com/noelschwarz/coral/actions`.

### 4. Edit the draft Release

1. Go to https://github.com/noelschwarz/coral/releases.
2. The draft Release `coralbridge 0.6.0` is waiting for you.
3. Upload `coral-extension.zip` as an additional asset (drag-and-drop
   into the assets area).
4. Replace the templated body with the relevant section of
   `CHANGELOG.md` (paste the `## [0.6.0]` block).
5. Verify the four pre-publish checklist items in the template:
   - [ ] CI run end-to-end on macOS (manual today)
   - [ ] SHA-256 sums match `dist/SHA256SUMS`
   - [ ] Threat model reflects what's in the build
   - [ ] No `Partial` status remains in `THREAT_MODEL.md` §6.2
6. Click **Publish release**.

### 5. Approve the PyPI publish

1. Back in the Actions tab, the `publish-pypi` job is waiting on the
   `pypi` environment.
2. Click **Review deployments** → **Approve and deploy**.
3. PyPI receives the sdist + wheel via OIDC. ~30 seconds later the
   project is live at https://pypi.org/project/coralbridge/.

### 6. Smoke-test the published artifact

In a clean shell (or a fresh VM):

```sh
pip install coralbridge==0.6.0
coral --version       # should print 0.6.0
coral diagnose        # quick install self-check
```

### 7. Announce

- Post to GitHub Discussions ("Release" category).
- If launching to a wider audience (Show HN, Twitter), link to the
  release page rather than the bare repo URL.

## Troubleshooting

**"`publish-pypi` failed: trusted-publishing not configured."** — Step
1 of one-time setup isn't done; the pending publisher entry doesn't
match `owner/repo/workflow/environment` exactly. Re-check.

**"`publish-pypi` failed: `coralbridge` already has version 0.6.0
uploaded."** — Once a version is on PyPI, it can never be replaced or
re-uploaded. Bump to `0.6.1`, re-tag, re-run.

**"Tag verification failed: pyproject_version mismatch."** — The
`build` job refuses to proceed when the git tag's version doesn't
match `pyproject.toml`'s `[project] version`. Bump the file, commit,
delete the tag, and re-tag.

**Extension zip is huge or has hidden files.** — Always zip from
inside `extension/dist/` (`cd extension/dist && zip -r ../coral-extension.zip .`),
not from `extension/` — otherwise the zip's root contains the source
tree.
