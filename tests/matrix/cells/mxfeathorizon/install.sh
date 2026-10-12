# A real Laravel with Horizon and a real Redis behind it. This cell exists for
# one feature, `horizon`, whose command acts on Redis rather than on the
# database - the first thing in this matrix that does.
composer create-project --no-interaction --prefer-dist laravel/laravel .

# `APP_ENV=local` is load-bearing, not decoration. Laravel defaults APP_ENV to
# `production`, and `horizon:clear` runs through ConfirmableTrait, which
# refuses in production without `--force`; the engine execs non-interactively,
# so the command would return 1 and clear nothing - and the feature is
# `allow_failure: true`, so the sync would call that fine. The real
# laravel/laravel skeleton ships APP_ENV=local, so this is what a project
# looks like, not a workaround.
#
# `QUEUE_CONNECTION=redis` because the skeleton ships `database`, and a job on
# the database queue is not in the queue Horizon clears. The profile's env step
# rewrites this key to `sync` on every sync, which would take Horizon's queue
# out of use; the cell's `ddev-sync.yaml` overrides it back, and the comment
# there is where that interaction is written down. `REDIS_CLIENT=predis`
# because the skeleton ships `phpredis` and this image has no redis extension.
#
# What this .env deliberately does NOT set is `CACHE_STORE`. The laravel
# profile's setup runs `php artisan optimize:clear`, which includes
# `cache:clear`, and `Illuminate\Cache\RedisStore::flush()` is a `flushdb()`
# (vendor/laravel/framework/src/Illuminate/Cache/RedisStore.php) - it wipes
# the whole Redis database, every `queues:*` key included, so a Redis cache
# store would empty the planted queue and leave the cell green for an effect
# `horizon:clear` did not cause. What rules that out is the profile, not this
# silence: its env step pins `CACHE_STORE: file` on every sync. The key is
# left unset so the recipe does not state a value the profile then contradicts
# - unset, the pre-sync store is `database`, which `config/cache.php` declares
# (`env('CACHE_STORE', 'database')`) and the skeleton's `.env.example` ships.
cat > .env <<'ENV'
APP_ENV=local
APP_KEY=base64:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
APP_URL=https://mxfeathorizon.ddev.site
DB_CONNECTION=mysql
DB_HOST=ddev-mxfeathorizon-db
DB_PORT=3306
DB_DATABASE=db
DB_USERNAME=db
DB_PASSWORD=db
QUEUE_CONNECTION=redis
REDIS_CLIENT=predis
REDIS_HOST=ddev-mxfeathorizon-redis
REDIS_PORT=6379
ENV

php artisan migrate --force --no-interaction

# The same deliberately unapplied migration the other Laravel cells leave: this
# cell runs the laravel profile too, and the migrate assertion is keyed on the
# profile rather than on the cell name.
php artisan make:migration create_matrix_marker_table --create=matrix_marker --no-interaction

# predis rather than the phpredis extension, which this image does not build.
# Horizon lists ext-pcntl and ext-posix in `require`, so without the pcntl this
# image now builds, this line fails at composer's platform check.
composer require --no-interaction laravel/horizon predis/predis

# A job class for the marker to push, because a closure cannot be pushed at
# all from here: Laravel serializes a queued closure by reading the source
# file the closure was written in, and `php artisan tinker --execute=` evals
# its argument - so dispatching a closure that way dies with "Failed to
# serialize job of type [Illuminate\Queue\CallQueuedClosure]:
# file_get_contents(eval()'d code)" before anything reaches Redis (measured
# against laravel/framework v13.35.0). The generated job's `handle()` is
# empty, which is all the marker needs: what is asserted is that one entry
# sits in the queue Horizon is about to clear, not what the entry would do.
php artisan make:job MatrixMarkerJob --no-interaction

composer show laravel/framework | grep -E '^(name|versions) '
composer show laravel/horizon | grep -E '^(name|versions) '
composer show predis/predis | grep -E '^(name|versions) '
php artisan --version
php -r 'var_dump(extension_loaded("pcntl"));'
php -v | head -1
composer --version
