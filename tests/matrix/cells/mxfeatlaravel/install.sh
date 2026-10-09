# A real Laravel with four features installed the way a project installs
# them. The profile side of this cell is `mxlaravel`'s claim, not this one's -
# what is new here is that `filament`, `livewire`, `inertia` and `node-build`
# are all activatable afterwards, and that every path their commands have to
# create is deleted again before the sync runs. Without that deletion an
# `appears` assertion passes against a path the install left behind, and
# proves nothing (tests/matrix/cells.py::FeatureCheck).
composer create-project --no-interaction --prefer-dist laravel/laravel .

cat > .env <<'ENV'
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://mxfeatlaravel.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-mxfeatlaravel-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
ENV

php artisan migrate --force --no-interaction

# The same deliberately unapplied migration `mxlaravel` leaves behind. This
# cell runs the laravel profile too, and
# `test_artisan_migrate_applied_the_pending_migration` is keyed on the
# profile, not on the cell name - so the premise has to hold here as well.
php artisan make:migration create_matrix_marker_table --create=matrix_marker --no-interaction

# The three packages whose features this cell proves, unconstrained like the
# skeleton above: the VERIFIED.md row records whatever composer resolved on
# the day. All three are detected by `composer_package` (features/*.yaml), so
# installing them is what makes the features activate.
composer require --no-interaction \
  filament/filament livewire/livewire inertiajs/inertia-laravel

# A panel, because `filament:optimize` caches panels and there is nothing to
# cache without one. A real Filament project runs this once at install time.
php artisan filament:install --panels --no-interaction

# node-build cannot be activated at all without a lockfile:
# `features/package_manager.py::detect_package_manager` keys on
# package-lock.json / yarn.lock / pnpm-lock.yaml and nothing else, and
# `laravel/laravel` ships none of the three (checked at v13.11.0: all three
# paths 404). This one install writes package-lock.json - which `npm ci`, the
# command `${PM_INSTALL}` resolves to, also refuses to run without - and
# populates node_modules.
npm install --no-audit --no-fund

# Every path the sync's own feature commands must create, removed again:
#   public/build                     <- ${PM_RUN} build (node-build)
#   public/vendor/livewire           <- livewire:publish --assets
#   bootstrap/cache/filament         <- filament:optimize, "Caching components"
#   bootstrap/cache/blade-icons.php  <- filament:optimize, "Caching Blade icons"
# The npm install above may itself have written public/build, and
# `filament:install` writes the two caches, so this is a removal and not just
# a precaution.
rm -rf public/build public/vendor/livewire \
       bootstrap/cache/filament bootstrap/cache/blade-icons.php

# The lockfile, on the other hand, must survive: without it the feature is
# not activated and the whole node-build claim silently becomes a skip.
test -f package-lock.json || {
  echo "npm install left no package-lock.json: node-build cannot be activated" >&2
  exit 1
}

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment the VERIFIED.md rows will claim to have been verified
# against - the three feature packages included.
composer show laravel/framework | grep -E '^(name|versions) '
composer show filament/filament | grep -E '^(name|versions) '
composer show livewire/livewire | grep -E '^(name|versions) '
composer show inertiajs/inertia-laravel | grep -E '^(name|versions) '
php artisan --version
node -v
npm -v
php -v | head -1
composer --version
