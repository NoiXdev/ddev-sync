# syntax=docker/dockerfile:1

# The builder carries uv and the sources; the runtime carries neither. What
# ships is an interpreter, the installed wheel and five binaries - no build
# tools, no compiler, no uv.
FROM python:3.13-alpine@sha256:2dd78ad5cf13a0b68f5134dc49aa9950203a8cf4b7463431b9f3b398287c5059 AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /src
# `uv build` installs no dependencies, so the lock file is not needed here -
# only the metadata and the package itself.
COPY pyproject.toml ./
COPY src ./src
RUN uv build --wheel --out-dir /dist

FROM python:3.13-alpine@sha256:2dd78ad5cf13a0b68f5134dc49aa9950203a8cf4b7463431b9f3b398287c5059

# The five binaries the engine shells out to, and nothing else:
# - openssh-client-default  transport/ssh.py (ssh, scp, and the -J jump host).
#                           `openssh-client` is only a virtual name this
#                           package provides, so `apk policy` cannot resolve a
#                           version for it; the real package is pinned instead.
# - rsync           the files steps; the client side of an rsync-over-ssh pull
# - mariadb-client  operations/database.py and the preflight table probe
# - rclone          operations/s3.py
# - docker-cli      transport/container.py, for `docker exec` into the web
#                   container; the sidecar deliberately has no PHP, because
#                   the PHP a project needs is the project's own
# Pinned with `=`: when a branch rotates a package the build fails naming it,
# which is the point. The base image is digest-pinned so that failure is
# reproducible rather than a moving target.
RUN apk add --no-cache \
      openssh-client-default=10.3_p1-r1 \
      rsync=3.5.0-r0 \
      mariadb-client=11.8.8-r0 \
      rclone=1.74.1-r2 \
      docker-cli=29.8.2-r0

COPY --from=builder /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl

# Where profiles/registry.py and features/registry.py look when they are not
# running from a checkout (src/ddev_sync/paths.py::existing_or_fallback).
COPY profiles /opt/ddev-sync/profiles
COPY features /opt/ddev-sync/features

# DDEV mounts the project here.
WORKDIR /var/www/html

# The image's own default is a non-root user (base design §8.2), which is also
# what makes the agent's `proxy-socket` the only usable socket (§12). The
# add-on's compose service overrides this with the developer's own uid so
# files written into the project belong to them - a default, not a ceiling.
# Not named `sync`: Alpine already ships a system account of that name.
RUN adduser -D -u 1000 ddevsync
USER ddevsync

ENTRYPOINT ["ddev-sync"]
CMD ["--help"]
