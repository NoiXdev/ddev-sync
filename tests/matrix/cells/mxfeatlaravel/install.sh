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
# package-lock.json / yarn.lock / pnpm-lock.yaml and nothing else, and `npm
# ci` - the command `${PM_INSTALL}` resolves to - refuses to run without one
# either. The skeleton above is installed unconstrained, so whether the
# version of the day ships a lockfile is not something this recipe can know;
# this install writes one regardless, and populates node_modules. The check
# below is what holds that to account.
npm install --no-audit --no-fund

# Every path the sync's own feature commands must create, removed again:
#   public/build                     <- ${PM_RUN} build (node-build)
#   public/vendor/livewire           <- livewire:publish --assets
#   bootstrap/cache/filament         <- filament:optimize, "Caching components"
#   bootstrap/cache/blade-icons.php  <- filament:optimize, "Caching Blade icons"
# A precaution, not a removal of anything known to be there: nothing above
# writes any of the four. `npm install` runs no build - the skeleton's
# package.json declares only `build` and `dev` and no `postinstall`, and
# `npm run build` lives in composer's `setup` script, which this recipe never
# runs - and `filament:install`'s single `call()` is `filament:upgrade`,
# which reaches `filament:assets` plus config/route/view:clear and nothing
# that writes bootstrap/cache (the install tail prints "Successfully
# upgraded!" and no "Caching components"). It stays because a future version
# that did leave one of these behind would make that path's assertion vacuous
# with nothing to say so, and `rm -rf` on an absent path costs nothing.
rm -rf public/build public/vendor/livewire \
       bootstrap/cache/filament bootstrap/cache/blade-icons.php

# The lockfile, on the other hand, must survive: without it the feature is
# never activated (features/resolver.py yields no layer for a detected
# feature whose commands use `${PM_*}` when no lockfile is found). Nothing
# would go quiet if it vanished - `cell.features` is a registry constant, so
# neither feature body can skip, and both would fail. They would fail four
# minutes later and read as the sync's fault; this names the cause where the
# cause is.
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
