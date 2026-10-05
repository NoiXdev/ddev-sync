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
