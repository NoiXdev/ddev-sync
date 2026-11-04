#!/bin/sh
# Prepare the endpoint, then become sshd.
#
# Three things happen here, and each one exists because it cannot be done at
# build time: the session's uid is chosen by whoever deploys the stack, the
# authorised keys are theirs too, and the host key has to outlive the
# container. Everything that can go wrong with the three stops the endpoint
# with a message naming the setting - an endpoint that starts anyway is one
# that fails later, deeper, and on someone else's machine.
set -eu

ACCOUNT=sync
HOME_DIR=/home/sync
HOST_KEY=/etc/ssh/keys/ssh_host_ed25519_key
MOUNTED_KEYS=/etc/ddev-sync/authorized_keys
COMPOSED_KEYS=/run/ddev-sync/authorized_keys
HANDOVER_FILE=/run/ddev-sync/app.env

# refuse MESSAGE [URL]: the message, then the docs entry that explains it as
# the last line, so it is the one a log reader ends on.
refuse() {
    printf 'ddev-sync-endpoint: %s\n' "$1" >&2
    if [ -n "${2:-}" ]; then printf 'ddev-sync-endpoint: More: %s\n' "$2" >&2; fi
    exit 1
}

say() {
    printf 'ddev-sync-endpoint: %s\n' "$1"
}

# One positive integer. Empty or unset means the default (33), applied before
# this runs; a set value that is a name, a float or 0 is a configuration error,
# not something to round to a default.
is_id() {
    case "$1" in
        "" | *[!0-9]*) return 1 ;;
    esac
    [ "$1" -gt 0 ]
}

uid="${DDEV_SYNC_UID:-33}"
gid="${DDEV_SYNC_GID:-33}"
is_id "$uid" || refuse "DDEV_SYNC_UID=${uid}: the endpoint runs its sessions as this uid and will not run them as root or under a name. Set it to the numeric uid that owns the application files this stack mounts here." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-uid-or-gid"
is_id "$gid" || refuse "DDEV_SYNC_GID=${gid}: the endpoint runs its sessions with this gid and will not run them as the root group or under a name. Set it to the numeric gid that owns the application files this stack mounts here." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-uid-or-gid"

# Rewritten in place rather than recreated with `adduser`: the uid or gid a
# stack asks for may already belong to one of the image's own system accounts
# (gid 82 is `www-data` on Alpine 3.21, and 82 is exactly the uid an
# Alpine-based application image tends to run as), and `addgroup -g 82` fails
# on that. Duplicate ids in these files are legal and resolve by name, which
# is how sshd looks the account up. Checked afterwards, because a `sed` that
# matched nothing would otherwise leave the session on the image's default
# uid and read the mounts it was given with the wrong one.
sed -i "s|^${ACCOUNT}:x:[0-9]*:[0-9]*:|${ACCOUNT}:x:${uid}:${gid}:|" /etc/passwd
sed -i "s|^${ACCOUNT}:x:[0-9]*:|${ACCOUNT}:x:${gid}:|" /etc/group
[ "$(id -u "$ACCOUNT")" = "$uid" ] && [ "$(id -g "$ACCOUNT")" = "$gid" ] ||
    refuse "could not give ${ACCOUNT} uid ${uid} and gid ${gid}" "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-uid-or-gid"
chown "${uid}:${gid}" "$HOME_DIR"

# A union of both sources, not a choice between them: they are two places to
# keep the same list, and composing them removes the need to pick (spec
# decision 6). Root-owned and 0644: sshd drops to the account to open this
# file, so it has to be readable by it, and StrictModes only asks that nobody
# else can write it.
# The directory is in the image, but a stack that mounts a tmpfs over /run -
# a common hardening recipe - replaces it, and busybox's "can't create ...:
# nonexistent directory" is not one of this file's named refusals.
mkdir -p "$(dirname "$COMPOSED_KEYS")"
chmod 755 "$(dirname "$COMPOSED_KEYS")"
: > "$COMPOSED_KEYS"
chmod 644 "$COMPOSED_KEYS"
if [ -n "${DDEV_SYNC_AUTHORIZED_KEYS:-}" ]; then
    printf '%s\n' "$DDEV_SYNC_AUTHORIZED_KEYS" >> "$COMPOSED_KEYS"
fi
if [ -f "$MOUNTED_KEYS" ]; then
    cat "$MOUNTED_KEYS" >> "$COMPOSED_KEYS"
fi
# A line that is neither blank nor a comment. The file existing is not the
# question: an endpoint with a comment-only key file would listen, be
# reachable, and be impossible to enter.
grep -qE '^[[:space:]]*[^#[:space:]]' "$COMPOSED_KEYS" ||
    refuse "no authorized keys. Set DDEV_SYNC_AUTHORIZED_KEYS, or mount a key file at ${MOUNTED_KEYS}, or both - the endpoint does not start without one." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#no-authorized-keys"
# And they have to *be* keys. A key pasted with CRLF line endings, or
# truncated by a mangled multi-line secret, passes the check above and
# produces the one state worth refusing outright: an endpoint that listens, is
# reachable, and cannot be entered. `ssh-keygen -l` is already in this image
# for the host key, so this costs nothing it does not already carry.
ssh-keygen -l -f "$COMPOSED_KEYS" > /dev/null 2>&1 ||
    refuse "the authorized keys are not readable as public keys. Check for truncated lines or CRLF line endings in DDEV_SYNC_AUTHORIZED_KEYS and ${MOUNTED_KEYS}." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#no-authorized-keys"

# sshd starts a session with a clean environment, so a credential the stack
# sets as a runtime variable on this container is invisible to the sync. The
# variables DDEV_SYNC_EXPORT_ENV lists - or all of them, for the word `all` -
# are written to a file the session's own account can read, and the engine
# reads it from there.
# Removed first: an endpoint restarted without the list must not leave the
# previous run's credentials behind.
rm -f "$HANDOVER_FILE"
if [ -n "${DDEV_SYNC_EXPORT_ENV:-}" ]; then
    # An `=` anywhere means a value was pasted where names belong - as
    # `NAME=value`, or with spaces around the `=` so the value stands as a
    # name of its own. Refused before the list is split, and without quoting
    # any of it: whichever piece is the value, it would reach the log.
    case "$DDEV_SYNC_EXPORT_ENV" in
        *=*) refuse "DDEV_SYNC_EXPORT_ENV holds an =. List the names of the variables to hand over, not NAME=value; their values come from the container's environment." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-name-invalid" ;;
    esac
    # The one word `all` hands over everything the stack gave this container.
    # A comma keeps it a list, where `all` is a variable name like any other:
    # `all,DB_HOST` is not "everything, and DB_HOST".
    every=""
    set -f
    # shellcheck disable=SC2086
    set -- $DDEV_SYNC_EXPORT_ENV
    if [ "$#" = 1 ] && [ "$1" = all ]; then every=1; fi

    # Commas and whitespace both separate, because a stack file writes the list
    # either way. Globbing is off until the names are validated: a name
    # like `*` must reach that check as itself, not as a directory listing.
    names=""
    if [ -z "$every" ]; then
        for name in $(printf '%s' "$DDEV_SYNC_EXPORT_ENV" | tr ',' ' '); do
            case " ${names} " in
                *" ${name} "*) ;;
                *) names="${names} ${name}" ;;
            esac
        done
    fi

    # Every name is checked before any of them is looked up: the name is
    # handed to a command below and written into the file as the left of a
    # `NAME=value` line, and one that is not a plain identifier would make
    # the line mean something else. The DDEV_SYNC_ prefix is the endpoint's
    # own settings, which include the authorised keys and have no business in
    # a handover.
    for name in $names; do
        case "$name" in
            DDEV_SYNC_* | *[!A-Za-z0-9_]* | [0-9]*)
                refuse "DDEV_SYNC_EXPORT_ENV lists ${name}, which is not a variable name the endpoint can hand over. Names are letters, digits and underscores, do not start with a digit, and the endpoint's own DDEV_SYNC_ settings are not available." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-name-invalid"
                ;;
        esac
    done
    set +f

    mkdir -p "$(dirname "$HANDOVER_FILE")"
    chmod 755 "$(dirname "$HANDOVER_FILE")"
    # Under a umask of its own so the file is never readable by anyone for the
    # moment between creating it and restricting it; moved into place so a
    # reader never sees it half written. Put back afterwards: the host key
    # and the files sshd reads are not meant to inherit it.
    previous_umask="$(umask)"
    umask 077
    staged="$(dirname "$HANDOVER_FILE")/.app.env.$$"
    printf '# ddev-sync endpoint handover v1\n' > "$staged"
    count=0
    skipped_names="$(dirname "$HANDOVER_FILE")/.app.skipped.$$"
    : > "$skipped_names"
    if [ -n "$every" ]; then
        # Walked entry by entry, as the lookup below is. A name that is not a
        # plain identifier would make its line mean something else, so it is
        # left out like the endpoint's own settings; a value with a line break
        # cannot be carried, and there is no one to refuse it to - its name is
        # kept for the sync to report, never its value. `-s` lifts busybox's
        # 32 KiB line limit above the 128 KiB the kernel allows one entry.
        xargs -0 -n 1 -s 200000 sh -c '
            case $3 in *=*) ;; *) exit 0 ;; esac
            name=${3%%=*}
            case $name in
                "" | DDEV_SYNC_* | *[!A-Za-z0-9_]* | [0-9]*) exit 0 ;;
            esac
            value=${3#*=}
            case $value in
                *"
"* | *"$(printf "\r")"*) printf "%s\n" "$name" >> "$2" ;;
                *) printf "%s=%s\n" "$name" "$value" >> "$1" ;;
            esac
        ' sh "$staged" "$skipped_names" < "/proc/$$/environ" ||
            refuse "could not read the container's environment." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-variable-unset"
        count=$(($(wc -l < "$staged") - 1))
    fi
    for name in $names; do
        # From the environment the container was started with, not from this
        # shell: a variable inherited from it is the shell's own, so a listed
        # ACCOUNT or HOME_DIR would otherwise be handed over as whatever this
        # script set it to. `printenv` alone does not escape that - the
        # assignment is exported along with the variable - so it is read from
        # /proc, which the shell never writes to. One entry per command: a
        # large environment does not fit on one command line, and xargs would
        # split it without saying so. Nothing printed is unset; an `x` on each
        # side marks it set, keeps an empty value apart from that, and keeps
        # a trailing line break from being eaten by `$(...)`. What is left is
        # byte for byte what the stack set.
        value="$(xargs -0 -n 1 -s 200000 sh -c 'case $1 in "$0="*) printf "x%sx" "${1#*=}" ;; esac' "$name" < "/proc/$$/environ")" ||
            refuse "could not read ${name} from the container's environment." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-variable-unset"
        [ -n "$value" ] ||
            refuse "DDEV_SYNC_EXPORT_ENV lists ${name}, but ${name} is not set in this container. Pass it to the endpoint through environment: or env_file:, or remove it from the list." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-variable-unset"
        value="${value#x}"
        value="${value%x}"
        # One line per variable is the whole format. A value with a line break
        # in it would split into two, and the second would be read as a
        # variable of its own. The message names the variable and never the
        # value: it is a credential, and this goes to a log.
        case "$value" in
            *"
"* | *"$(printf '\r')"*)
                refuse "${name} holds a line break, which the handover file cannot carry: it keeps one variable per line. Use a value without one." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-value-has-a-newline"
                ;;
        esac
        printf '%s=%s\n' "$name" "$value" >> "$staged"
        count=$((count + 1))
    done
    # Last, and only when there is something to say: the sync reports these
    # names, so a stack that left a variable out is told which one.
    skipped=0
    if [ -s "$skipped_names" ]; then
        skipped="$(wc -l < "$skipped_names")"
        printf 'DDEV_SYNC_SKIPPED=%s\n' "$(paste -sd, "$skipped_names")" >> "$staged"
    fi
    rm -f "$skipped_names"
    chown "${uid}:${gid}" "$staged"
    chmod 400 "$staged"
    mv "$staged" "$HANDOVER_FILE"
    umask "$previous_umask"
    if [ "$skipped" -gt 0 ]; then
        say "handing over ${count} variables to the sync session, skipped ${skipped} with a line break"
    else
        say "handing over ${count} variables to the sync session"
    fi
fi

# Said out loud either way: a developer whose connection was just refused as a
# changed host key needs to know whether this container generated a new one.
if [ -f "$HOST_KEY" ]; then
    say "reusing the host key from the volume"
else
    ssh-keygen -q -t ed25519 -N '' -f "$HOST_KEY"
    say "generated a host key"
fi

# `-e` so sshd's log goes to stderr, where `docker logs` and a compose
# healthcheck can see it, instead of into a syslog socket no container has.
exec /usr/sbin/sshd -D -e
