# syntax=docker/dockerfile:1

# The far side of `provider: docker`: an sshd that lives inside a production
# stack and carries the server half of what the engine invokes over ssh, and
# nothing else. It is reached through the Docker host by ProxyJump, so the
# developer who syncs needs ordinary SSH to that host and no Docker rights at
# all - which is the whole point of this image existing.
#
# Digest-pinned like the engine image, so the build does not move under us.
FROM alpine:3.21@sha256:ce64758a109eb420d874a118f87920e625e12d3634e03b4a5573fd9f6e5d3507

# What the engine runs on the far side, and not a fourth thing:
# - openssh-server   every remote call: `ssh user@host sh -c ...`
#                    (operations/preflight.py's probe, operations/database.py's
#                    dump) and the `rsync --server` process an rsync-over-ssh
#                    pull talks to
# - openssh-keygen   the entrypoint's host key; also what openssh-server needs
# - rsync            the server side of the files steps (operations/files.py)
# - mariadb-client   `mysqldump` for the dump and `mysql` for the preflight's
#                    `SHOW TABLES`. Alpine's package still provides those
#                    compatibility names beside `mariadb-dump`/`mariadb`; a
#                    test asserts each one, because the package is free to
#                    drop them.
#
# Deliberately absent: `docker-cli`, which would move the privilege this
# design removes rather than remove it; `rclone`, because S3 is pulled by the
# developer's side; PHP and wp-cli, because WordPress's `wp_cli` credential
# source is refused under this provider (spec decision 7) rather than served
# here; and `openssh-sftp-server`, because nothing in the engine calls
# `scp_download_command` - if that ever changes, modern scp speaks SFTP and
# this image needs the subsystem.
#
# Pinned with `=`: when a branch rotates a package the build fails naming it,
# which is the point.
RUN apk add --no-cache \
      openssh-server=9.9_p2-r0 \
      openssh-keygen=9.9_p2-r0 \
      rsync=3.5.0-r0 \
      mariadb-client=11.4.13-r0

# Alpine ships an account called `sync` of its own (`sync:x:5:0`, the classic
# `/bin/sync` shell), and the engine connects as `sync` - fixed by
# config/models.py::ENDPOINT_USER, not configurable. So the system account is
# removed and replaced rather than worked around.
#
# The uid baked here is only a default: the entrypoint rewrites it from
# DDEV_SYNC_UID, because the uid that owns a stack's application files is
# chosen at deploy time and cannot be known here. gid 33 is free on 3.21,
# which is why the default can be created with `addgroup` at all.
#
# `/etc/ssh/keys` is the host-key volume's mount point, read by sshd as root,
# so it stays root-owned and mode 700. `/run/ddev-sync` holds the composed
# authorized_keys and is deliberately *readable* (0755/0644, root-owned):
# sshd drops to the target account to open AuthorizedKeysFile, so a 0600
# root-owned file is refused as "Permission denied (publickey)" - measured.
# Public keys are not secret; what StrictModes cares about, and what this
# keeps, is that nobody but root can write them.
#
# `sed` on /etc/shadow because `adduser -D` leaves the password field `!`,
# which sshd running as root reads as "User sync not allowed because account
# is locked" - also measured, and invisible to a non-root sshd, which is how
# it survives a casual probe. `*` is the field for an account that has no
# password and never will: key authentication only.
RUN deluser sync \
 && addgroup -g 33 sync \
 && adduser -D -u 33 -G sync -h /home/sync sync \
 && sed -i 's|^sync:!:|sync:*:|' /etc/shadow \
 && grep -q '^sync:\*:' /etc/shadow \
 && mkdir -p /etc/ssh/keys /etc/ddev-sync /run/ddev-sync \
 && chmod 700 /etc/ssh/keys \
 && chmod 755 /run/ddev-sync

COPY sshd_config /etc/ssh/sshd_config
COPY --chmod=0755 entrypoint.sh /usr/local/bin/ddev-sync-endpoint

EXPOSE 22

# In the image, so every stack that runs this endpoint inherits it: an sshd
# that died leaves a container that is still `Up`, nothing restarts it, and
# the first symptom is a developer's failed sync days later. Port 22 in LISTEN
# read out of /proc, which needs none of the binaries this image deliberately
# does not have.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD grep -qiE ':0016 [0-9A-F]+:0000 0A' /proc/net/tcp

ENTRYPOINT ["/usr/local/bin/ddev-sync-endpoint"]
