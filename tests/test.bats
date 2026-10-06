#!/usr/bin/env bats

# The DDEV-standard add-on test. It is the only test in this repository that
# exercises installation, so it is also the only one that can catch a manifest
# that installs nothing, a command DDEV runs in the wrong service, or a removal
# that leaves files behind. The Python suites cannot see any of that.
#
# Run it from the add-on root (needs bats-core, Docker and DDEV):
#   bats tests/test.bats
# Only what can pass before anything is published:
#   bats tests/test.bats --filter-tags '!release'
#
# Tagging follows the DDEV add-on template: the release test carries
# `# bats test_tags=release` and the directory test carries no tag, which is
# why CI selects with `--filter-tags '!release'` and `--filter-tags release`.

setup() {
  set -eu -o pipefail

  # The release test only skips; building an image and configuring a project
  # for it would be wasted time, and a failure here would fail a test that is
  # meant to be skipped.
  case "${BATS_TEST_DESCRIPTION}" in *release*) return 0 ;; esac

  export DIR="$(cd "$(dirname "${BATS_TEST_FILENAME}")/.." >/dev/null 2>&1 && pwd)"
  export PROJNAME="ddevsync-bats"
  export DDEV_NONINTERACTIVE=true
  export DDEV_NO_INSTRUMENTATION=true
  # TESTDIR is set before anything that can fail, because teardown runs even
  # when setup dies half way and has to know what to remove.
  export TESTDIR=""
  # Under $HOME rather than the system temp directory, as in the DDEV add-on
  # template: Docker providers on macOS share $HOME with their VM by default,
  # but not necessarily the per-user temp directory.
  mkdir -p "${HOME}/tmp"
  TESTDIR="$(mktemp -d "${HOME}/tmp/${PROJNAME}.XXXXXX")"

  # This writes the developer's real ~/.ddev/ddev-sync/config.yaml and puts back
  # whatever was there. Not because the engine insists on that path - it reads
  # whatever `--home` names - but because both alternatives are worse here. A
  # stand-in home outside $HOME is not shared with Docker's VM on macOS, for the
  # reason given just above, so the mount would come up empty; and overriding
  # HOME wholesale would move DDEV's own global_config.yaml with it, changing
  # what the ssh-agent test is testing.
  #
  # The three variables are exported before anything that can fail, because
  # teardown has to restore even after a setup that died half way;
  # GLOBAL_WRITTEN is what tells teardown that the file below is the suite's to
  # delete and not the developer's.
  export GLOBAL_CONFIG="${HOME}/.ddev/ddev-sync/config.yaml"
  export GLOBAL_BACKUP=""
  export GLOBAL_WRITTEN=""
  mkdir -p "$(dirname "${GLOBAL_CONFIG}")"
  if [ -e "${GLOBAL_CONFIG}" ]; then
    GLOBAL_BACKUP="$(mktemp "${GLOBAL_CONFIG}.bats-backup.XXXXXX")"
    mv "${GLOBAL_CONFIG}" "${GLOBAL_BACKUP}"
  fi
  # A host under `.invalid` never resolves, so nothing here can reach anything
  # even if a test were to try.
  cat > "${GLOBAL_CONFIG}" <<'YAML'
version: 1
targets:
  bats-global:
    host: ddev-sync-global.invalid
    user: deploy
    path: /srv/global/
    stage: production
YAML
  GLOBAL_WRITTEN=yes

  # A project left over from an interrupted earlier run would make `ddev
  # config` below refuse the name.
  ddev delete -Oy "${PROJNAME}" >/dev/null 2>&1 || true

  cd "${TESTDIR}"
  mkdir -p public && echo '<?php phpinfo();' > public/index.php
  ddev config --project-name="${PROJNAME}" --project-type=php --docroot=public >/dev/null

  # The order below matters. The add-on's pre-start hook runs on every start
  # and reads /etc/passwd out of the sync image, so the image must exist and
  # .ddev/.env must name it before the first `ddev restart`; otherwise the hook
  # fails and the whole project refuses to start. The published image does not
  # exist until the release chain lands, so the suite builds one locally.
  # Plan 8 removes these two steps.
  docker build -q -t ddev-sync:bats "${DIR}" >/dev/null
  printf 'DDEV_SYNC_IMAGE=ddev-sync\nDDEV_SYNC_VERSION=bats\n' >> .ddev/.env
}

# Unconditional on purpose: it must clean up after a failed assertion and after
# a failed setup alike, or every red CI run would leak a project and its
# containers. Nothing here may abort before the directory is removed, so the
# `set -eu` that setup left active in this shell is switched off, and every
# step tolerates the thing it removes being absent. Setup returns early for the
# release test, so PROJNAME and TESTDIR may both be unset.
teardown() {
  set +eu
  if [ -n "${PROJNAME:-}" ]; then
    ddev delete -Oy "${PROJNAME}" >/dev/null 2>&1 || true
  fi
  cd "${HOME}" || true
  if [ -n "${TESTDIR:-}" ]; then
    rm -rf "${TESTDIR}"
  fi
  # The global configuration layer is the developer's own file, so the one the
  # suite wrote goes away again and anything that was there comes back,
  # whatever the test did.
  if [ -n "${GLOBAL_WRITTEN:-}" ]; then
    rm -f "${GLOBAL_CONFIG}"
  fi
  if [ -n "${GLOBAL_BACKUP:-}" ] && [ -e "${GLOBAL_BACKUP}" ]; then
    mv "${GLOBAL_BACKUP}" "${GLOBAL_CONFIG}"
  fi
}

# Counts what is inside a directory without caring about its name. A missing
# directory counts as empty, which is the right answer for "nothing left".
entries_in() {
  if [ -d "$1" ]; then
    find "$1" -mindepth 1 | wc -l | tr -d ' '
  else
    echo 0
  fi
}

@test "install from directory, use it, remove it" {
  set -eu -o pipefail
  cd "${TESTDIR}"

  run ddev add-on get "${DIR}"
  echo "${output}"
  [ "${status}" -eq 0 ]

  # Everything the manifest lists is on disk. Without this a manifest that
  # installs nothing would pass every later assertion that happens to be lax.
  for f in \
    .ddev/docker-compose.ddev-sync.yaml \
    .ddev/config.ddev-sync.yaml \
    .ddev/commands/sync/sync \
    .ddev/ddev-sync/ddev-sync.example.yaml \
    .ddev/ddev-sync/prepare-sidecar.sh \
    .ddev/ddev-sync/.gitignore; do
    [ -f "${f}" ] || { echo "not installed: ${f}"; false; }
    # DDEV deletes a file on removal only when it carries this marker, so a
    # file without it would survive `ddev add-on remove` unnoticed.
    grep -q '#ddev-generated' "${f}" || { echo "no #ddev-generated marker: ${f}"; false; }
  done

  # The project's own configuration was created from the template, it is real,
  # and it is NOT marked as generated, which is what keeps removal from
  # deleting it. `version: 1` is asserted because the install action strips the
  # template's header with a sed range that runs to the end of the file if the
  # template loses its blank line, and sed would still exit 0: the result would
  # be an empty configuration that the action's own existence check then
  # refuses to repair.
  [ -f .ddev/ddev-sync.yaml ]
  grep -q '^version: 1$' .ddev/ddev-sync.yaml
  run grep -c 'ddev-generated' .ddev/ddev-sync.yaml
  [ "${output}" = 0 ]

  # A pre-start hook runs here, so this is also where a missing or wrong image
  # reference would stop the project from starting.
  run ddev restart -y
  echo "${output}"
  [ "${status}" -eq 0 ]

  # The pre-start hook has run: the facts it discovers per machine exist.
  grep -q '^DDEV_SYNC_DOCKER_GID=[0-9][0-9]*$' .ddev/.env
  # -F -x: the home directory is matched as a whole line and literally, so a
  # path with a regex metacharacter in it cannot turn the assertion into
  # something else.
  grep -q -F -x "DDEV_SYNC_HOST_HOME=${HOME}" .ddev/.env
  [ -s .ddev/ddev-sync/passwd ]
  [ -d .ddev/ddev-sync/home ]

  # The engine answers through the command. DDEV only finds this command inside
  # the sync service if the compose file mounts .ddev where DDEV looks, and it
  # only runs it there if it sits in commands/sync/; the engine's version
  # string cannot come from anywhere else, since the host and the web
  # container have no `ddev-sync`.
  # Every `[[ ]]` below ends in `|| false` on purpose: bash before 4.1, which
  # includes the 3.2 that macOS ships and that bats runs under there, does not
  # apply errexit to a failing `[[ ]]` unless it is the last command of the
  # test, so a bare one is a silent no-op on a developer's machine.
  run ddev sync --version
  echo "${output}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"ddev-sync, version "* ]] || false

  # Arguments reach the engine rather than being eaten by DDEV. `--version`
  # above proves the least of that class, because DDEV understands that flag
  # too; a bare subcommand and `--help` are the two shapes DDEV is most likely
  # to answer itself. The string asserted is one only the engine prints:
  # `ddev-sync` is the engine's own program name, which no help DDEV generates
  # for `ddev sync` would use.
  run ddev sync --help
  echo "${output}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"Usage: ddev-sync"* ]] || false

  # The global configuration layer is reachable from inside the sidecar. It is
  # the one layer that lives outside the project, in the developer's own home,
  # and nothing of that home exists in the container by itself: without the
  # compose file's mount and the --home in commands/sync/sync, the engine
  # resolves the layer to a path that can never exist, and a target defined
  # there is absent with nothing said about why. The layer's label is asserted
  # as well as the target's name, because the label is what says the definition
  # came from that file rather than from the project's own.
  run ddev sync targets
  echo "${output}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"bats-global"* ]] || false
  [[ "${output}" == *"deploy@ddev-sync-global.invalid:/srv/global/"* ]] || false
  [[ "${output}" == *"[~/.ddev/ddev-sync/config.yaml]"* ]] || false

  # The mount is read-only, so the developer's global configuration cannot be
  # rewritten from inside the container by anything the engine or a sync runs.
  run ddev exec -s sync sh -c 'echo x >> /mnt/ddev-sync-host-home/.ddev/ddev-sync/config.yaml'
  echo "${output}"
  [ "${status}" -ne 0 ]

  # What the sidecar exists to provide. `doctor` needs a target to run at all,
  # so the project's configuration (which is the test's to edit; it is not
  # generated) gets one that cannot be reached: a name under `.invalid` never
  # resolves. The ssh rows are therefore expected to FAIL, and so is the exit
  # status; what is under test are the two rows that do not depend on the
  # remote. `doctor` probes the docker socket inside its web-container check,
  # so there is no row of its own for it, and an OK there means the socket is
  # mounted, usable by the developer's uid and able to reach the web container.
  # The agent row is a filesystem check on the forwarded agent socket; it needs
  # no key to be loaded, so this test never runs `ddev auth ssh`.
  #
  # `--target` is named rather than left to the engine's own choice because the
  # global layer above defines a second target, and with more than one the
  # engine asks - which it cannot do here, since there is no terminal. Naming
  # it also puts a flag that takes a value through DDEV's argument handling.
  cat >> .ddev/ddev-sync.yaml <<'YAML'

targets:
  unreachable:
    host: ddev-sync-test.invalid
    user: deploy
    path: /srv/app/
    stage: production
YAML
  run ddev sync --target unreachable doctor
  echo "${output}"
  [ "${status}" -ne 0 ]
  [[ "${output}" =~ FAIL\ +ssh\ connection ]] || false
  [[ "${output}" =~ OK\ +web\ container ]] || false
  [[ "${output}" =~ OK\ +ssh\ agent ]] || false

  # ssh must be able to run as the developer's uid. The image knows uid 1000
  # only, and the container runs as the developer's uid, so OpenSSH used to
  # abort in getpwuid() with "No user exists for uid" before it read a single
  # option. `ssh -V` does not reach that call; `-G` does, and it connects to
  # nothing.
  run ddev exec -s sync ssh -G example.com
  echo "${output}"
  [ "${status}" -eq 0 ]
  [[ "${output}" == *"hostname example.com"* ]] || false

  run ddev add-on remove ddev-sync
  echo "${output}"
  [ "${status}" -eq 0 ]

  # Everything generated is gone. DDEV deletes the files it installed and
  # leaves their directories behind, because `removal_actions` run before the
  # files are deleted; so the directories are asserted empty, not absent.
  for f in \
    .ddev/docker-compose.ddev-sync.yaml \
    .ddev/config.ddev-sync.yaml \
    .ddev/commands/sync/sync \
    .ddev/ddev-sync/ddev-sync.example.yaml \
    .ddev/ddev-sync/prepare-sidecar.sh \
    .ddev/ddev-sync/.gitignore; do
    [ ! -e "${f}" ] || { echo "survived removal: ${f}"; false; }
  done
  # What the pre-start hook generated, which is not in the manifest's list and
  # is removed by its removal action instead.
  [ ! -e .ddev/ddev-sync/passwd ]
  [ ! -e .ddev/ddev-sync/home ]
  [ "$(entries_in .ddev/ddev-sync)" = 0 ]
  [ "$(entries_in .ddev/commands/sync)" = 0 ]
  run grep -c -e '^DDEV_SYNC_DOCKER_GID=' -e '^DDEV_SYNC_HOST_HOME=' .ddev/.env
  [ "${output}" = 0 ]

  # ...and what is not generated stays: the project's own configuration, and
  # the lines the test itself put into .env.
  [ -f .ddev/ddev-sync.yaml ]
  grep -q '^version: 1$' .ddev/ddev-sync.yaml
  grep -q '^DDEV_SYNC_IMAGE=ddev-sync$' .ddev/.env
}

# The sidecar mounts the volume of DDEV's ssh agent, which DDEV declares only
# while the agent is not omitted. Without the pre-start hook's check the whole
# project would refuse to start with "refers to undefined volume", which names
# neither the add-on nor the cause. The omission is set for this project only,
# so the test leaves the developer's global DDEV configuration alone.
@test "refuses to start with a message when DDEV's ssh agent is switched off" {
  set -eu -o pipefail
  cd "${TESTDIR}"

  run ddev add-on get "${DIR}"
  echo "${output}"
  [ "${status}" -eq 0 ]

  ddev config --omit-containers=ddev-ssh-agent >/dev/null

  run ddev restart -y
  echo "${output}"
  [ "${status}" -ne 0 ]
  # What the user needs: the reason, and which list to take the agent out of.
  # The blanket `ddev config global --omit-containers=` is deliberately not the
  # advice, since it would also switch back on anything else omitted globally.
  [[ "${output}" == *"Remove ddev-ssh-agent from omit_containers"* ]] || false
  [[ "${output}" == *"ddev-sync needs it"* ]] || false
}

# bats test_tags=release
@test "install from release" {
  set -eu -o pipefail
  # It cannot pass until the release chain publishes something, and a skip
  # with its reason is honest where a stub that asserts nothing would not be.
  skip "nothing is published yet - Plan 8 adds the release and the image"
}
