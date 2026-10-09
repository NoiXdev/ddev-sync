# The web stand-in: the PHP image a cell names, plus what a real DDEV web
# container has and a bare `php:*-cli` lacks. The engine's setup commands
# (`composer install`, `php artisan ...`) and the installers run in here, and
# none of them can work without composer, a PDO MySQL driver, git/unzip for
# composer's dist downloads and a MariaDB client for the schema dump.
#
# TYPO3 additionally refuses to install without `intl`, `zip` and `gd`
# (composer checks `ext-*` requirements before it unpacks anything), and its
# default database driver is `mysqli`, so those are built here too. The -dev
# packages stay installed rather than being purged afterwards: the image is a
# throwaway test fixture, and purging would only add a fragile step.
ARG PHP_IMAGE
FROM ${PHP_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git unzip mariadb-client \
      libicu-dev libzip-dev libpng-dev libjpeg-dev libfreetype6-dev \
 && rm -rf /var/lib/apt/lists/* \
 && docker-php-ext-configure gd --with-freetype --with-jpeg \
 && docker-php-ext-install -j"$(nproc)" pdo_mysql mysqli intl zip gd

# Pinned to an exact version rather than `composer:2`: composer's policy
# defaults are an input to the install recipes, not a detail of the image.
# 2.10.3 refuses by default to install a package covered by a security
# advisory, which is why the TYPO3 recipes pass `--no-blocking` to
# `create-project` and name this version while doing it. A floating `2` moves
# that behaviour under those comments without anyone choosing it - which is how
# the blocking arrived here in the first place. Bump it deliberately, and
# re-read those recipes when doing so.
COPY --from=composer:2.10.3 /usr/bin/composer /usr/local/bin/composer

# wp-cli, on the PATH rather than downloaded by a recipe: the engine reaches
# it by `docker exec` (`wp search-replace`, `wp user create`, `wp cache
# flush`), and an exec'd process sees the image's PATH, not whatever an
# install recipe exported. A real DDEV web container ships it for the same
# reason. Taken from the wp-cli project's own build repository; the WordPress
# cell's recipe prints the version it resolved to. It needs nothing beyond the
# `mysqli` extension built above and a larger memory limit: a bare `php:*-cli`
# has no php.ini, so PHP's built-in 128M applies, and `wp core download` runs
# out of it while extracting WordPress. DDEV's own web container raises it too.
ADD --chmod=0755 https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar /usr/local/bin/wp
RUN echo 'memory_limit = 512M' > /usr/local/etc/php/conf.d/zz-matrix-memory.ini

# node and npm, for the same reason as wp-cli above: the engine runs
# `${PM_INSTALL}` / `${PM_RUN} build` (features/node-build.yaml) by `docker
# exec`, which sees this image's PATH and not an install recipe's exports. A
# real DDEV web container ships node too.
#
# Copied from the official node image rather than installed from apt, because
# Debian's `npm` package trails: the Laravel skeleton's package.json pins
# `vite ^8`, which refuses to run on anything below node 20.19 / 22.12.
#
# `bookworm`, while `php:8.3-cli` and `php:8.2-cli` are both trixie: a binary
# built against an older glibc runs on a newer one, and the reverse does not,
# so the older base is the safe one to copy from whichever way the php images
# move next. Measured: node v22.23.3 and npm 10.9.9 run on both php bases.
#
# npm is a package under /usr/local/lib/node_modules, not a binary, so the
# node image's own symlinks are recreated here rather than copied.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
