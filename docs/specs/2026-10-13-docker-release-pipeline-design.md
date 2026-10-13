# Docker release pipeline design

**Goal.** Publish the engine image to Docker Hub and cut a public pre-release
`v0.1.0-rc.1`, through a reusable workflow owned by `NoiXdev/github-workflows`
rather than a fourth copy of a release script. After this lands, the first real
sync against a real remote host can be run against the artifact a user would
actually pull.

**Status of the thing being released.** `README.md:76-77` states that no real
sync against a real remote host has ever been run through the add-on, and
`profiles/VERIFIED.md` names every variant whose commands no run has touched
(`typo3@9`, `typo3@10`, `shopware6`, and the two base rows). Those statements
stay true and stay published. They are the reason this is a release candidate
and not `0.1.0`.

## Context, measured

Facts this design rests on, each read rather than recalled:

- `docker-compose.ddev-sync.yaml:17` resolves the sidecar image as
  `${DDEV_SYNC_IMAGE:-noixdev/ddev-sync}:${DDEV_SYNC_VERSION:-0.1.0}`. Nothing
  with that name exists on Docker Hub today.
- `ddev-sync/prepare-sidecar.sh:230` repeats both defaults, because the
  pre-start hook has to resolve the same image to read `/etc/passwd` out of it
  and cannot ask compose what it would use. The file's own comment warns that
  changing one without the other makes the hook read the user database of a
  different image than the service runs.
- The Docker Hub namespace `noixdev` exists and holds four public repositories
  (`marketix`, `inventorix`, `kontorfix`, `fleetorio`). `ddev-sync` is not among
  them; Docker Hub creates a repository on first push.
- `NoiXdev/marketix`, `NoiXdev/inventorix` and `NoiXdev/fleetorio` each carry
  their own `.github/workflows/release.yml` with `docker/build-push-action`.
  There is no shared Docker release workflow, although
  `NoiXdev/github-workflows/README.md` states that workflows should be built so
  they are actually reusable.
- `marketix/release.yml` establishes the house conventions this design adopts:
  Docker Hub addressed through `vars.DOCKERHUB_NAMESPACE` /
  `vars.DOCKERHUB_IMAGE` and `secrets.DOCKERHUB_USERNAME` /
  `secrets.DOCKERHUB_TOKEN`; tags computed by `docker/metadata-action`; release
  commits authored as `noix-dev-bot`; the changelog action run text-only; a
  `dry_run` input. On `marketix` only `DOCKERHUB_IMAGE` is a repository
  variable, so the namespace and both secrets live at organisation level.
- `marketix/release.yml` records that the repository policy
  `sha_pinning_required` rejects a branch-ref action while the action graph is
  resolved — before any step exists, so `continue-on-error` does not contain it
  — and that this cost `v2.0.3` its announcement.
- `NoiXdev/github-workflows/actions/compute-version` reads the current version
  with `node -p "require('./' + process.env.VERSION_FILE).version"`, so its
  version file must be **JSON**. It bumps first and appends the prerelease
  identifier: from `0.1.0` with `prerelease: rc` it yields `0.1.1-rc.1`
  (`patch`) or `0.2.0-rc.1` (`minor`). Calling it again with the same
  identifier increments the prerelease counter; calling it with an empty
  identifier promotes `X.Y.Z-rc.N` to `X.Y.Z` and ignores the bump. It fails
  when the computed tag already exists, and derives `is-prerelease` from the
  version rather than taking it as a flag.
- `.github/workflows/matrix.yaml` already accepts an `image` input through
  `workflow_call`; an empty value makes it build an amd64 image from the ref and
  hand it to every cell, which is how the nightly run works. Its own comment
  says publishing the image is the release pipeline's job once the matrix is
  green. `.github/workflows/ci.yaml:62-63` says the same about the multi-arch
  build and the push.
- `.github/workflows/ci.yaml` has no `workflow_call` trigger, so nothing can
  call its gates today.
- `version.json` holds `{"version": "0.1.0"}`, is referenced by no file in the
  repository, and has existed unchanged since the scaffold commit `027a5ad`.
- `pyproject.toml:7` holds `version = "0.1.0"`, and
  `src/ddev_sync/cli/main.py:185` renders `--version` from
  `importlib.metadata.version("ddev-sync")`, so the installed wheel's metadata
  is what the image reports.
- `NoiXdev/github-workflows` has an **unmerged** branch `chore/pin-actions`
  (three commits ahead of `main`) that changes two things this design must
  follow. First, every third-party action is pinned to a full commit SHA with
  the concrete version in a trailing comment, and the README states the rule:
  a moving tag means whatever its owner points it at next runs here, and these
  workflows cut releases for every NoiX product. The pinned SHA is what the
  *referenced* tag pointed at, not the newest release, so pinning to latest
  would be a silent multi-major upgrade wearing the clothes of a lockdown. The
  `NoiXdev/github-workflows/actions/...@main` self-references stay floating
  deliberately. Second, it adds `create_go_changelog_version_release.yaml` and
  `blueprints/release-go.yaml`, whose trigger is `workflow_run` on CI
  completion rather than a push or a tag — so that only the default branch and
  only a green commit can release, by construction rather than by a check.
- The house pins this design reuses, read from that branch:
  `actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0`,
  `actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020 # v4.4.0`,
  `TriPSs/conventional-changelog-action@ee43def3241429afddab215ba89090cbf64fb45c # v6.4.0`,
  `softprops/action-gh-release@3bb12739c298aeb8a4eeaf626c5b8d85266b0e65 # v2.6.2`.
  The five Docker actions are not yet used anywhere in that repository; their
  SHAs were resolved against `git/matching-refs/tags` and are recorded in the
  implementation plan, with the concrete version each one is.
- A bare `ddev add-on get <owner>/<repo>` installs a pre-release. Read at the
  add-on's declared floor and at the version this was measured on:
  `pkg/ddevapp/addons.go::GetAddonTarballURL` calls
  `github.GetGitHubRelease(owner, repo, "")`, and that function
  (`pkg/github/github.go`, v1.25.0 and v1.25.4 alike) calls `ListReleases` and
  takes `releases[0]` with **no pre-release filter at all**. So `v0.1.0-rc.1`
  as the only release is installable by the plain command, and goal 3 needs no
  `--version`.
  *The other edge of the same finding:* because the first listed release wins,
  a candidate published **after** a final release becomes what new installs get
  on DDEV v1.25.x. DDEV's current `main` has since changed this to
  `GetLatestRelease` with a fallback that only runs on a 404, which would
  prefer the final release. Cutting an `rc` after `0.1.0` is therefore not a
  free act and needs its own decision when it arises; publishing one now, with
  nothing final in existence, is unambiguous.
- `tests/test.bats:341-346` already holds a `# bats test_tags=release` test
  named `install from release` whose body is a single `skip` reading "nothing
  is published yet". `.github/workflows/addon.yaml` runs the suite twice —
  `--filter-tags '!release'` and `--filter-tags release` — and the second step
  asserts through the TAP plan that exactly one test was selected and that it
  skipped, with a comment saying the `# skip` assertion is what has to be taken
  out the day the release exists.
- `NoiXdev/ddev-sync` is a private repository with no releases and no topics.
- Nothing sensitive is committed: `tests/integration/.keys/`,
  `tests/integration/fixtures/remote/` and the matrix work trees are ignored,
  and no key, certificate or environment file appears anywhere in history. The
  repository carries an MIT `LICENSE`.
- `tests/test_no_legacy_terms.py` is the precedent for a repository-level
  consistency test: it resolves `REPO_ROOT` from `__file__`, walks
  `git ls-files`, and fails on content that must not exist.

## Goals

1. A reusable Docker release workflow in `NoiXdev/github-workflows` that
   ddev-sync calls and that the three existing products could adopt unchanged.
2. A multi-architecture image (`linux/amd64`, `linux/arm64`) on
   `docker.io/noixdev/ddev-sync`, because DDEV users run Apple Silicon.
3. A published GitHub pre-release `v0.1.0-rc.1`, so
   `ddev add-on get NoiXdev/ddev-sync` works.
4. One version, carried coherently by every file that names one, enforced by a
   test rather than by a comment.
5. Nothing published before this ref's gates and verification matrix are green.

## Non-goals

- Listing the add-on in the DDEV add-on registry. That belongs to the final
  release, not to a candidate.
- Migrating `marketix`, `inventorix` or `fleetorio` onto the new workflow. Each
  carries product-specific build steps (Composer, npm, a deployment bundle);
  each migration is its own change and its own decision.
- Verification matrix cells on arm64. The cells install real frameworks and run
  on amd64 runners; arm64 cells are a separate project.
- The four open follow-ups in
  `docs/plans/2026-10-12-service-feature-verification-follow-ups.md`, and
  verification of `typo3@9`, `typo3@10` or `shopware6`.

## Decisions

### 1. The publish lives in a new shared reusable workflow

`create_docker_changelog_version_release.yaml` in
`NoiXdev/github-workflows/.github/workflows/`, with a blueprint beside the
other three and a README entry. ddev-sync is its first caller.

*Why.* Three repositories already duplicate this script, and the org's own
README asks for reusability. A new file cannot break an existing caller, so
this costs nothing to the products that keep their local workflow until they
choose to move.

*If wrong.* A workflow generalised around one product's needs fits the other
three badly, and they never migrate. Mitigated by taking `marketix`'s proven
step order as the base and turning only the product-specific parts into inputs
with defaults that reproduce `marketix`'s current behaviour.

### 2. Verification comes before the push

The caller runs three gates in order before anything is published: the CI gates
(`ci.yaml`, which gains a `workflow_call` trigger), then the verification matrix
(`matrix.yaml` with `image: ""`, every cell), then the release.

*Why.* The repository's discipline is that a claim needs a measurement. A
release that publishes first and verifies afterwards inverts that. Both
workflows already exist and need no new machinery — the matrix call is exactly
the nightly path.

*Cost, stated.* Two builds per release: one amd64 image for the cells, one
multi-arch image for the push. The published image is built from the same ref,
but it is not the same build, and it is built after the version files are
rewritten. The difference between the verified tree and the built tree is
exactly the version strings and `CHANGELOG.md`.

*Rejected alternative.* Push to a throwaway tag, verify that artifact, promote
it by digest with `buildx imagetools create`. It verifies precisely what ships,
but it puts intermediate tags in a public namespace and moves digest promotion
into a workflow three other products inherit. It also verifies only the amd64
half, as the chosen path does.

### 3. The image is built after the version is written

*Why.* `--version` comes from the installed wheel's metadata
(`src/ddev_sync/cli/main.py:185`), so an image built before the bump reports the
previous version. `marketix/release.yml` makes the same choice and says so at
its npm version step.

### 4. Push the image before committing and tagging

*Why.* Neither order is atomic, so the question is which orphan is cheaper. A
pushed image whose release failed is an image tag nobody references, and the
same tag can be pushed again. A pushed git tag whose image push failed is a tag
in a public repository that others may already have fetched, and moving or
deleting it is the expensive kind of mistake. `marketix/release.yml` uses this
order; this design keeps it and records why.

### 5. `version.json` is the version source of truth; a repo script propagates it

`compute-version` reads JSON, and `version.json` is already JSON, already in the
repository, and read by nothing — so it becomes the file the pipeline reads and
bumps. A new `tools/set_version.py` writes the computed version into every
other place that names one.

*Why a repo script rather than a shared action.* How a version reaches
`pyproject.toml` and two shell defaults is ddev-sync's business, not the org's.
The shared workflow takes the script as an input, which is the pattern
`create_tauri_changelog_version_release.yaml` already uses for its
`macos_build_script`.

*Interface.* The workflow invokes the script as `"./$VERSION_SCRIPT" "$VERSION"`
with both values passed through the environment, never interpolated into the
shell line — the reason `compute-version` gives for its own `env:` block. The
script therefore needs a shebang and the executable bit, and uses only the
standard library so the runner needs no dependency install.

### 6. The first tag is `v0.1.0-rc.1`, reached by resetting the version to `0.0.0`

`compute-version` bumps before it appends an identifier, so `0.1.0-rc.1` is
unreachable from `0.1.0`. A preparatory commit sets the version to `0.0.0`
everywhere; `bump: minor` with `prerelease_id: rc` then computes exactly
`0.1.0-rc.1`.

*Why not `0.1.1-rc.1`.* `0.1.0` was never published. A first public tag of
`0.1.1-rc.1` would assert a `0.1.0` that never existed.

*What it buys later.* From `0.1.0-rc.1`, a second candidate is `rc.2`, and an
empty `prerelease_id` promotes it to `0.1.0` final.

### 7. A pre-release gets exactly one image tag

`docker/metadata-action` is configured as `marketix` configures it: `latest`,
`{{major}}.{{minor}}` and `{{major}}` are all gated on the release *not* being a
pre-release, leaving `{{version}}` as the only tag a candidate publishes.

*Why.* A candidate that moved `latest` would hand itself to everyone who pins
nothing. Note that this means the compose default must name the full
`0.1.0-rc.1`, not a shortened form — see decision 9.

### 8. Multi-arch on one runner through QEMU, smoke-tested natively per arch

The build uses `docker/setup-qemu-action` and a `platforms` input. After the
push, a smoke job pulls the published tag on `ubuntu-latest` **and**
`ubuntu-24.04-arm` and runs the checks `ci.yaml` runs against a local build: it
starts with `--network none`, carries all eight binaries, resolves profiles from
`/opt/ddev-sync/profiles`, and reports the released version.

*Why not a native two-runner build.* It is faster, but it brings per-platform
digest export and `imagetools create` into a workflow three products inherit
that do not need it.

*Measurement to take.* The first run records how long the arm64 leg takes under
emulation. If it is unreasonable, the split-runner build is the documented
fallback — a change to the shared workflow, not to ddev-sync.

### 9. The add-on's own texts change in this same change

Three places tell the user there is no image and to build one:
`README.md:17-40`, `install.yaml:99-107`, and the failure message at
`ddev-sync/prepare-sidecar.sh:256`. All three are rewritten to say that an image
is published, that this version is a release candidate, and how to pin it.

*Why here and not afterwards.* A pipeline that publishes an image while the
install message sends the user off to build their own is worse than no pipeline:
it makes the project's own instructions wrong.

The rewritten instruction is the plain `ddev add-on get NoiXdev/ddev-sync`,
with no `.env` lines at all: DDEV installs from the release tarball, that
tarball carries the compose default written by the release commit, and the
measurement above shows the plain command reaches a pre-release. The two
`.env` lines the current text asks for become unnecessary, not merely
different — and the development install from a clone stays documented as what
it is.

*The sharp edge.* `docker-compose.ddev-sync.yaml:17` and
`ddev-sync/prepare-sidecar.sh:230` carry the default version. If a default names
a tag that does not exist, a freshly installed project does not fail at sync
time — it fails at `ddev start`, and the whole project refuses to start. This is
what decision 10 exists for.

### 10. Version coherence is a test, not a comment

`tests/test_version_coherence.py` reads all four machine-readable places and
fails unless they agree: `pyproject.toml:7`, `version.json`,
the compose default in `docker-compose.ddev-sync.yaml:17`, and the `setting`
default in `ddev-sync/prepare-sidecar.sh:230`. It runs in the ordinary gate, so
a hand edit to one of them is red before it is pushed.

`tools/set_version.py` fails loudly when a pattern it is asked to rewrite does
not match exactly once. A regex that silently matched zero places is how this
drift happens in the first place.

*Why a test.* The compose file already warns about this in prose. The warning is
correct and has no teeth.

### 11. The trigger is a dispatch, not a CI completion

The caller is `workflow_dispatch`, like
`create_tauri_changelog_version_release.yaml`, and not `workflow_run` on CI
completion, like the newer `create_go_changelog_version_release.yaml`.

*Why.* A release here is a deliberate act that needs three inputs the commits
cannot supply: which part to bump, whether this is a candidate, and whether to
rehearse. A `workflow_run` trigger releases on every green push to the default
branch, which is right for a product whose version follows its commits and
wrong for one cutting a numbered candidate.

*What that costs, and how it is paid.* The Go blueprint's argument is sound:
with `workflow_run` it is impossible to release a red commit or a non-default
branch, by construction rather than by a check. This design keeps both
guarantees differently — `release` declares `needs: [gates, verify]`, so the
gates and the full matrix run **on the ref being released** rather than on an
earlier run that is trusted to still apply, and the caller guards the branch
with `if: github.ref == 'refs/heads/main'`. That is a stronger statement about
the artifact than inheriting a previous run's conclusion, at the price of
running the gates again.

### 12. The new workflow follows the pin convention and branches from it

Every third-party action in the new workflow is pinned to a full commit SHA
with its concrete version in a trailing comment; the
`NoiXdev/github-workflows/actions/...@main` self-reference to
`compute-version` stays floating, as the README says those deliberately do.

The work branches from `chore/pin-actions`, not from `main`: that branch
carries the convention the new file has to follow, and basing on `main` would
either write an unpinned file or conflict with the branch on every shared line.

*Consequence, stated plainly.* The caller references the shared workflow at
`@main`. So `chore/pin-actions` and the new workflow must both land on
`github-workflows`' `main` before ddev-sync's release can run at all. That is
in the prerequisites.

### 13. The repository becomes public

*Why.* `ddev add-on get NoiXdev/ddev-sync` reads the release through the public
API; without it the "public pre-release" is only an image. Two further
consequences: Actions minutes are free for public repositories, which matters
for a matrix of one job per cell, and `ubuntu-24.04-arm` runners become
available, which is what makes the native arm64 smoke job in decision 8
possible.

*What is exposed.* The full history, including `docs/specs/` and `docs/plans/`
with their development narrative, and `profiles/VERIFIED.md` with its explicit
list of what has never been run. Nothing sensitive: no keys, no environment
files, MIT licensed.

## The shared workflow

`create_docker_changelog_version_release.yaml`, `on: workflow_call`.

**Inputs.** Defaults chosen so that a caller written like `marketix`'s current
workflow behaves as it does today.

| Input | Type | Default | Meaning |
|---|---|---|---|
| `bump` | string | required | `patch`, `minor` or `major` |
| `prerelease_id` | string | `""` | `rc`, `beta`, …; empty produces a final version |
| `prerelease` | boolean | `false` | Force the pre-release marking on an otherwise final version |
| `dry_run` | boolean | `false` | Run everything, push nothing: no image, no commit, no tag, no release |
| `platforms` | string | `linux/amd64` | Comma-separated build platforms |
| `version_file` | string | `package.json` | JSON file holding the current version; `compute-version` reads it |
| `version_script` | string | `""` | Executable in the repo, invoked with the new version; empty means the workflow writes only `version_file` |
| `product_name` | string | `""` | Name in the release announcement; empty uses the repository name |
| `release_files` | string | `""` | Paths attached to the GitHub release |

ddev-sync leaves `release_files` empty — DDEV installs from the source tarball
GitHub attaches to every release by itself. The input exists because
`marketix` attaches a deployment bundle, and a workflow that cannot express
that is a workflow it can never adopt, which is decision 1's whole purpose.

**Outputs.** `tag`, `version`, `is_prerelease`, `release_url`, `image_digest`.

**Steps, in order.**

1. Validate the Docker Hub configuration — the two variables and two secrets —
   and fail naming the missing one. `marketix` does this first for a reason: a
   missing secret otherwise surfaces as an authentication error from the
   registry, halfway through.
2. `actions/checkout` with `fetch-depth: 0` and `ref: main`.
3. `actions/setup-node` — `compute-version` needs `node`.
4. `compute-version` with `bump`, `prerelease_id`, `version_file`.
5. Write the version: `version_file` always — rewritten through `node`, which
   step 3 installed and which `compute-version` already relies on, so the
   workflow needs no second tool to edit JSON — and then `version_script`,
   when one was given.
6. Re-read `version_file` and fail unless it holds the computed version. This
   is `marketix`'s "Verify version is in sync" step, and it is what catches a
   `version_script` that silently did nothing.
7. `TriPSs/conventional-changelog-action` with `skip-bump`, `skip-tag`,
   `skip-commit`, `skip-version-file` all on — text only. The version is owned
   by step 4.
8. `setup-qemu-action` and `setup-buildx-action`.
9. `docker/login-action`, skipped on a dry run.
10. `docker/metadata-action`: `{{version}}` always; `latest`,
    `{{major}}.{{minor}}` and `{{major}}` only when this is not a pre-release.
    OCI labels for title, source, revision, version and licence.
11. `docker/build-push-action` with `platforms`, `provenance: mode=max`,
    `sbom: true`, `push` gated on the dry run, GitHub Actions cache on both
    ends.
12. Commit and tag as `noix-dev-bot`, skipped on a dry run. The commit message
    is `chore(release): <tag> [skip ci]`; the marker is what stops the release
    commit from starting another CI run. `git add -A`, as the Tauri workflow
    does and for its stated reason: a `version_script` may touch files at paths
    this workflow cannot predict, and a version written but left uncommitted is
    the worst of both outcomes.
13. `softprops/action-gh-release`, with `prerelease` set from
    `inputs.prerelease || is-prerelease == 'true'` — derived, so a release
    cannot be marked final while carrying a candidate version.
14. A step summary naming the previous and new version, the bump, both flags,
    and the image tags.

An `announce` job follows, gated on `dry_run == false`, calling
`NoiXdev/github-workflows/actions/announce-release@main` with
`continue-on-error`, exactly as `marketix` does.

## The caller

`.github/workflows/release.yaml` in ddev-sync. `workflow_dispatch` with `bump`
(choice), `prerelease_id` (default `rc`) and `dry_run`. `concurrency: release`,
`permissions: contents: write`, and every job guarded by
`if: github.ref == 'refs/heads/main'` so a dispatch from another branch cannot
release — the guarantee decision 11 pays for by other means.

```
gates    uses ./.github/workflows/ci.yaml
verify   uses ./.github/workflows/matrix.yaml   with image: ""
release  needs [gates, verify]   uses NoiXdev/github-workflows/.github/workflows/create_docker_changelog_version_release.yaml@main
         with  bump, prerelease_id, dry_run,
               platforms: linux/amd64,linux/arm64,
               version_file: version.json,
               version_script: tools/set_version.py,
               product_name: ddev-sync
         secrets: inherit
smoke    needs release   matrix over [ubuntu-latest, ubuntu-24.04-arm]
```

`ci.yaml` gains `workflow_call:` alongside its existing triggers; nothing else
about it changes.

Two things to know about this graph. `matrix.yaml` declares
`concurrency: verification-matrix-${{ github.ref }}` with
`cancel-in-progress: false`, so a release dispatched while the nightly matrix is
running queues behind it rather than cancelling it. And the smoke job skips
itself on a dry run, because there is nothing published to pull.

## `tools/set_version.py`

Stdlib only, shebang, executable. Takes one argument, the version.

Rewrites, each pattern required to match exactly once, or the script exits
non-zero naming the file and the pattern:

| File | What changes |
|---|---|
| `pyproject.toml` | the `version = "…"` line in `[project]` |
| `version.json` | the `version` member |
| `docker-compose.ddev-sync.yaml` | the `${DDEV_SYNC_VERSION:-…}` default on line 17 |
| `ddev-sync/prepare-sidecar.sh` | the `setting DDEV_SYNC_VERSION …` default on line 230 |

Idempotent: running it twice with the same version changes nothing the second
time and still exits 0.

`version.json` is written by the script as well as by the workflow, so the two
agree; the workflow's own write is what `compute-version`'s contract expects,
and the duplicate write is harmless and keeps the script usable by hand.

Tests: `tests/test_set_version.py` drives it over temporary copies, including
the failure cases (a pattern that matches zero times, a pattern that matches
twice). `tests/test_version_coherence.py` reads the four real files, following
`tests/test_no_legacy_terms.py`'s shape.

## What arm64 means here

Verified on arm64 after this change: the image starts with no network, carries
every binary the engine invokes, resolves its profiles from the installed path,
and reports the released version.

Not verified on arm64: a sync. No cell runs there. This is recorded in
`README.md` and under *Known limitations* in `profiles/VERIFIED.md`, in those
words — the point of that file is that it does not overstate.

## Prerequisites the user performs

Each is outside what this change can do, and the release cannot run without it.

1. Make `NoiXdev/ddev-sync` public.
2. Push `main` — 26 commits are local only, and the workflow runs on GitHub.
3. Land `chore/pin-actions` **and** the new shared workflow on
   `NoiXdev/github-workflows`' `main`. The caller references the workflow at
   `@main`, so until both are there the release cannot resolve its own jobs.
4. Add the repository variable `DOCKERHUB_IMAGE=ddev-sync`.
5. Confirm the organisation carries `DOCKERHUB_NAMESPACE`,
   `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN` and
   `DISCORD_WEBHOOK_RELEASE_NOTIFICATION`, and that the Docker Hub token may
   create a repository in the `noixdev` namespace.
6. Confirm `sha_pinning_required` is off for this repository, and that `main`
   accepts a push from `GITHUB_TOKEN` — the release commits a version bump to
   it.

## Rollout

The first exercise is `dry_run: true`: it runs the gates, the full matrix, the
multi-arch build and the changelog, and pushes nothing — no image, no commit, no
tag, no release. Its step summary is the evidence that the pipeline is sound.
Only then the same dispatch with `dry_run: false`, `bump: minor`,
`prerelease_id: rc`, producing `v0.1.0-rc.1`.

## Risks

- **The compose default and the published tag disagree.** Consequence is a
  project that will not start at all. Guarded by decision 10's test, and by the
  dry run, whose diff shows every rewritten default before anything ships.
- **QEMU makes the arm64 leg slow.** Measured on the first run; the fallback is
  named in decision 8.
- **`sha_pinning_required` is on.** The release fails while the action graph
  resolves, before any step runs, and `continue-on-error` cannot contain it.
  This is in the prerequisites because it has already cost this organisation a
  release announcement once.
- **A candidate reaches someone who wanted a stable tool.** The release is
  marked as a pre-release, carries no `latest` tag, and the README and
  `VERIFIED.md` say what has never been run — including that no real sync
  against a real remote host has yet gone through the add-on.
