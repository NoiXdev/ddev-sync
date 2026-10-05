# The web stand-in: the PHP image a cell names, plus what a real DDEV web
# container has and a bare `php:*-cli` lacks. The engine's setup commands
# (`composer install`, `php artisan ...`) and the installers run in here, and
# none of them can work without composer, a PDO MySQL driver, git/unzip for
# composer's dist downloads and a MariaDB client for the schema dump.
ARG PHP_IMAGE
FROM ${PHP_IMAGE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends git unzip mariadb-client \
 && rm -rf /var/lib/apt/lists/* \
 && docker-php-ext-install pdo_mysql

COPY --from=composer:2 /usr/bin/composer /usr/local/bin/composer
