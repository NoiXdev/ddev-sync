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

# fail MESSAGE [FILE [HINT]]: the message, FILE's content indented under it,
# and a hint saying what to do about it. The hook runs before the containers
# start, so a failure here stops the whole project, not just the sidecar.
fail() {
  echo "ddev-sync: $1" >&2
  if [ -n "${2:-}" ] && [ -s "$2" ]; then sed 's/^/ddev-sync:   /' "$2" >&2; fi
  if [ -n "${3:-}" ]; then echo "ddev-sync: $3" >&2; fi
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
echo "ddev-sync: reading the docker socket's group (pulls ddev/ddev-utilities on first use)..." >&2
gid="$(docker run --rm \
  --mount type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock \
  ddev/ddev-utilities stat -c '%g' /var/run/docker.sock 2>"${err_file}")" \
  || fail "could not read the group of /var/run/docker.sock - is Docker running? Docker said:" "${err_file}"
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

# --- The sidecar's user: a passwd entry and a home directory -----------------
# The sidecar runs as the developer's uid so that files it writes belong to
# them, but the image only knows its own user. OpenSSH calls getpwuid() before
# it reads any option and aborts with "No user exists for uid" when there is
# no entry. musl reads /etc/passwd directly, so mounting a file that has the
# image's own entries plus one for this uid is all it takes.
#
# The image's file is the base, because replacing it with a single line would
# drop root and nobody. The image reference is resolved the way compose
# resolves it, shell environment before .ddev/.env, quotes removed; its
# defaults are repeated from docker-compose.ddev-sync.yaml rather than shared,
# because compose substitutes them itself and the only way to ask it what it
# would use is to run it, which needs the very values this script produces.
# Keep the two in step.
setting() {
  local value
  eval "value=\"\${$1:-}\""
  if [ -z "${value}" ] && [ -f "${env_file}" ]; then
    value="$(grep "^$1=" "${env_file}" | tail -n 1 | cut -d= -f2-)"
  fi
  value="${value:-$2}"
  # Compose strips one pair of surrounding quotes, so that is done here too.
  case "${value}" in
    \"*\" | \'*\') value="${value:1:${#value}-2}" ;;
  esac
  printf '%s' "${value}"
}
image="$(setting DDEV_SYNC_IMAGE noixdev/ddev-sync):$(setting DDEV_SYNC_VERSION 0.1.0)"

# Both are bind-mounted by the compose file. If the service ever starts
# without this script having run, Docker creates a missing source as an empty
# directory owned by root - for the passwd file that would make the `mv` below
# move the file into the directory and still exit 0.
passwd_file="${state_dir}/passwd"
home_dir="${state_dir}/home"
if [ -d "${passwd_file}" ]; then
  fail "${passwd_file} is a directory, which Docker creates when the service starts before this script has run. Remove it (sudo may be needed) and start again."
fi
if [ -e "${home_dir}" ] && { [ ! -d "${home_dir}" ] || [ ! -O "${home_dir}" ]; }; then
  fail "${home_dir} is not a directory of yours, which Docker creates when the service starts before this script has run. Remove it (sudo may be needed) and start again."
fi

uid="${DDEV_UID:?}"
new_tmp "${state_dir}/passwd"
passwd_tmp="${tmp}"
new_tmp "${state_dir}/passwd"
base_tmp="${tmp}"
# Pulling can take a while and the pull's own progress goes into the captured
# error file, so say what is happening before it starts.
echo "ddev-sync: reading the user database of ${image} (pulls the image on first use)..." >&2
docker run --rm --entrypoint cat "${image}" /etc/passwd > "${passwd_tmp}" 2>"${err_file}" \
  || fail "could not read /etc/passwd from the image ${image}. Docker said:" "${err_file}" \
    "The project cannot start without it. Until the image is published, build it from the ddev-sync checkout (docker build -t ddev-sync:dev .) and put DDEV_SYNC_IMAGE=ddev-sync and DDEV_SYNC_VERSION=dev in .ddev/.env."
if [ "${uid}" != 0 ]; then
  # A line for this uid already in the image (1000 is common on Linux) would
  # win over the new one, because the first match is returned.
  awk -F: -v uid="${uid}" '$3 != uid' "${passwd_tmp}" > "${base_tmp}"
  { cat "${base_tmp}"; echo "ddev:x:${uid}:${DDEV_GID:?}:ddev:/home/ddev:/bin/sh"; } > "${passwd_tmp}"
fi
chmod 644 "${passwd_tmp}"
mv "${passwd_tmp}" "${passwd_file}"

# The home directory the entry names. It is a bind mount rather than a tmpfs
# because ssh keeps known_hosts in it: a tmpfs would be empty again after every
# `ddev stop`, and every sync would ask the developer to trust the host anew.
# Created here, as the developer, so that it belongs to the uid the sidecar
# runs as.
mkdir -p "${home_dir}"
chmod 700 "${home_dir}"
