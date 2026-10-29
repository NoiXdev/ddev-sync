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
# variables DDEV_SYNC_EXPORT_ENV lists are written to a file the session's own
# account can read, and the engine reads it from there.
# Removed first: an endpoint restarted without the list must not leave the
# previous run's credentials behind.
rm -f "$HANDOVER_FILE"
if [ -n "${DDEV_SYNC_EXPORT_ENV:-}" ]; then
    # Commas and whitespace both separate, because a stack file writes the list
    # either way. Globbing is off until the names are validated: a name
    # like `*` must reach that check as itself, not as a directory listing.
    names=""
    set -f
    for name in $(printf '%s' "$DDEV_SYNC_EXPORT_ENV" | tr ',' ' '); do
        case " ${names} " in
            *" ${name} "*) ;;
            *) names="${names} ${name}" ;;
        esac
    done

    # Every name is checked before any of them is evaluated: the name is
    # spliced into an `eval` below, and one that is not a plain identifier
    # would be code. The DDEV_SYNC_ prefix is the endpoint's own settings,
    # which include the authorised keys and have no business in a handover.
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
    for name in $names; do
        eval "is_set=\${$name+x}"
        [ -n "$is_set" ] ||
            refuse "DDEV_SYNC_EXPORT_ENV lists ${name}, but ${name} is not set in this container. Pass it to the endpoint through environment: or env_file:, or remove it from the list." "https://docs.noix.dev/ddev-sync/reference/troubleshooting/#endpoint-export-env-variable-unset"
        eval "value=\${$name}"
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
    chown "${uid}:${gid}" "$staged"
    chmod 400 "$staged"
    mv "$staged" "$HANDOVER_FILE"
    umask "$previous_umask"
    say "handing over ${count} variables to the sync session"
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
