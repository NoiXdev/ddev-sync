# A real Laravel, with its own migrations creating its own schema in the
# LOCAL database. The schema is dumped from there to seed the stand-in
# remote, so every table a sanitize rule declares exists because Laravel
# created it - not because someone remembered it.
composer create-project --no-interaction --prefer-dist laravel/laravel .

cat > .env <<'ENV'
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://mxlaravel.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-mxlaravel-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
ENV

php artisan migrate --force --no-interaction

# A migration that exists in the project but has run nowhere, as it would on a
# branch ahead of production. The dump taken from the local database below
# therefore lacks its table, and the only thing that can create it after the
# sync is the profile's own `php artisan migrate --force` - which is
# `allow_failure`, so its effect is the only evidence it ran.
php artisan make:migration create_matrix_marker_table --create=matrix_marker --no-interaction

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment (PHP, composer, framework) the row in VERIFIED.md will
# claim to have been verified against.
composer show laravel/framework | grep -E '^(name|versions) '
php artisan --version
php -v | head -1
composer --version
