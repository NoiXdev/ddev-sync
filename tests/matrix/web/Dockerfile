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

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer

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
