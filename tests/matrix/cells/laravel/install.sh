# A real Laravel, with its own migrations creating its own schema in the
# LOCAL database. The schema is dumped from there to seed the stand-in
# remote, so every table a sanitize rule declares exists because Laravel
# created it - not because someone remembered it.
composer create-project --no-interaction --prefer-dist laravel/laravel .

cat > .env <<'ENV'
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://laravel.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-laravel-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
ENV

php artisan migrate --force --no-interaction

# Printed into the report: this is the version the row in VERIFIED.md will
# claim to have been verified against.
composer show laravel/framework | grep -E '^(name|versions) '
php artisan --version
