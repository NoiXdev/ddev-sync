#!/usr/bin/env bash
#ddev-generated
# Runs on the host before every `ddev start` / `ddev restart`, from the
# pre-start hook in .ddev/config.ddev-sync.yaml.
#
# It regenerates the two facts about the sidecar that belong to the machine it
# runs on rather than to the project: the group that owns the docker socket and
# a passwd entry for the developer's uid. Both used to be worked out once at
# install time, but a project's .ddev/ directory is committed and shared, so
# a teammate would inherit values discovered on somebody else's machine and
# never run the discovery themselves. Doing it at every start costs one
# short-lived container and cannot go stale.
set -eu

approot="${DDEV_APPROOT:?this script is meant to run as a DDEV hook}"
env_file="${approot}/.ddev/.env"
state_dir="${approot}/.ddev/ddev-sync"

# Every temporary file is registered here so that a failure removes it. The
# empty first element keeps `"${tmp_files[@]}"` valid under `set -u` in the
# bash 3.2 that macOS ships.
tmp_files=("")
cleanup() { rm -f "${tmp_files[@]}"; }
trap cleanup EXIT

# Creates a temporary file next to $1 and leaves its name in $tmp. It does not
# print it: a command substitution would run in a subshell and lose the
# registration above.
new_tmp() {
  tmp="$(mktemp "$1.XXXXXX")" || exit 1
  tmp_files+=("${tmp}")
}

fail() {
  echo "ddev-sync: $1" >&2
  if [ -n "${2:-}" ] && [ -s "$2" ]; then sed 's/^/ddev-sync:   /' "$2" >&2; fi
  exit 1
}

new_tmp "${TMPDIR:-/tmp}/ddev-sync-err"
err_file="${tmp}"

# --- The docker socket's group ------------------------------------------------
# Read from inside a container, not from the host: what matters is the group
# the socket has where the sidecar will see it. On a typical Linux that is the
# host's `docker` group, but under Docker Desktop the host path is a symlink
# whose own group (daemon) means nothing - the daemon runs in a VM where the
# same socket belongs to root, gid 0, and only that gid grants access. A
# host-side `stat` was verified to answer 1 there, which fails with a
# permission error that reads like a bug in the tool.
#
# `--mount` rather than `-v`: with `-v` a missing source is silently created as
# an empty directory, and `stat` would then report a plausible but bogus gid.
# ddev/ddev-utilities is the image DDEV itself uses for one-off commands, so it
# is normally present already.
gid="$(docker run --rm \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  ddev/ddev-utilities stat -c '%g' /var/run/docker.sock 2>"${err_file}")" \
  || fail "could not read the group of /var/run/docker.sock:" "${err_file}"
case "${gid}" in
  '' | *[!0-9]*) fail "unexpected group id '${gid}' for /var/run/docker.sock" ;;
esac

# Replace the line rather than append, and write through the existing file
# (`cat >`, not `mv`) so that its mode, and a symlink in its place, survive.
new_tmp "${env_file}"
env_tmp="${tmp}"
{ [ ! -f "${env_file}" ] || grep -v '^DDEV_SYNC_DOCKER_GID=' "${env_file}" || [ $? -eq 1 ]; } > "${env_tmp}"
echo "DDEV_SYNC_DOCKER_GID=${gid}" >> "${env_tmp}"
cmp -s "${env_tmp}" "${env_file}" 2>/dev/null || cat "${env_tmp}" > "${env_file}"

# --- A passwd entry for the developer's uid -----------------------------------
# The sidecar runs as the developer's uid so that files it writes belong to
# them, but the image only knows its own user. OpenSSH calls getpwuid() before
# it reads any option and aborts with "No user exists for uid" when there is
# no entry. musl reads /etc/passwd directly, so mounting a file that has the
# image's own entries plus one for this uid is all it takes.
#
# The image's file is the base, because replacing it with a single line would
# drop root and nobody. The image reference is resolved the way compose
# resolves it, shell environment before .ddev/.env; its defaults must match
# docker-compose.ddev-sync.yaml.
setting() {
  local value
  eval "value=\"\${$1:-}\""
  if [ -z "${value}" ] && [ -f "${env_file}" ]; then
    value="$(grep "^$1=" "${env_file}" | tail -n 1 | cut -d= -f2-)"
  fi
  printf '%s' "${value:-$2}"
}
image="$(setting DDEV_SYNC_IMAGE noixdev/ddev-sync):$(setting DDEV_SYNC_VERSION 0.1.0)"

uid="${DDEV_UID:?}"
new_tmp "${state_dir}/passwd"
passwd_tmp="${tmp}"
new_tmp "${state_dir}/passwd"
base_tmp="${tmp}"
docker run --rm --entrypoint cat "${image}" /etc/passwd > "${passwd_tmp}" 2>"${err_file}" \
  || fail "could not read /etc/passwd from the image ${image}:" "${err_file}"
if [ "${uid}" != 0 ]; then
  # A line for this uid already in the image (1000 is common on Linux) would
  # win over the new one, because the first match is returned.
  awk -F: -v uid="${uid}" '$3 != uid' "${passwd_tmp}" > "${base_tmp}"
  # The home directory is a tmpfs owned by this uid, see the compose file.
  { cat "${base_tmp}"; echo "ddev:x:${uid}:${DDEV_GID:?}:ddev:/home/ddev:/bin/sh"; } > "${passwd_tmp}"
fi
chmod 644 "${passwd_tmp}"
mv "${passwd_tmp}" "${state_dir}/passwd"
