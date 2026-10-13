# A real Laravel with Horizon and a real Redis behind it. This cell exists for
# one feature, `horizon`, whose command acts on Redis rather than on the
# database - the first thing in this matrix that does.
composer create-project --no-interaction --prefer-dist laravel/laravel .

# `APP_ENV=local` is here because the real laravel/laravel skeleton ships it,
# which makes this cell's .env independent of the profile rather than
# load-bearing against it. An earlier wording called it "load-bearing, not
# decoration"; that is false on this cell's ordered run. Laravel's own default
# is `production`, and `horizon:clear` goes through ConfirmableTrait, which
# refuses in production without `--force` - the engine execs
# non-interactively, so the prompt resolves to its default of false, the
# command returns 1 and clears nothing, and `allow_failure: true` reports that
# as fine. But what keeps this cell out of that state is the profile's env
# step: `profiles/laravel.yaml`'s `env.set` carries `APP_ENV: local`,
# `patch_keys` appends keys the file does not already have, and
# `engine/steps.py` puts ENV in the `db` group, ordered before SETUP. So on
# any sync that includes `db` - the default, and what the whole-sync test runs
# before the queue test - the key reads `local` by the time the command runs,
# whether or not the project ever set it. Deleting this line would not fail the
# ordered run. The refusal is reachable in exactly three places: a
# `--steps setup` run against a tree that was never synced, a project setting
# `env: strategy: none`, and a project override that pins APP_ENV itself.
#
# `QUEUE_CONNECTION=redis` because the skeleton ships `database`, and a job on
# the database queue is not in the queue Horizon clears. The profile defaults
# this key (`env.default`) rather than imposing it, so the sync leaves the
# line below as it is, and this cell's `ddev-sync.yaml` needs no override.
# That is the point of writing it here: the env test reads the key back after
# the sync, and a profile that took the queue away again would turn it red.
# `REDIS_CLIENT=predis` because the skeleton ships `phpredis` and this image
# has no redis extension.
#
# `CACHE_STORE=redis` is a contrary value, written on purpose: the one a
# developer running a Redis cache would have. The laravel profile's setup runs
# `php artisan optimize:clear`, which includes `cache:clear`, so the sync
# empties whatever cache store the project configured - and for a remote store
# that is somebody else's data (`MemcachedStore::flush()` flushes a whole
# server). That is why the profile's `env.set` imposes `CACHE_STORE: file` on
# every sync instead of leaving it to the project. Writing `redis` here makes
# that a measured overwrite: the env test reads `file` after the sync where
# this file says `redis`, so it fails if the key is ever defaulted or dropped
# instead. Unset, the key would read `file` after a sync either way and prove
# nothing; the registry test checks this line is still here.
#
# The contrary value does not endanger the queue evidence, and the reason is the
# skeleton's own config rather than luck: `config/database.php` gives the
# `default` Redis connection `env('REDIS_DB', '0')` and the `cache` connection
# `env('REDIS_CACHE_DB', '1')`. `RedisStore::flush()` is a `flushdb()` on the
# cache connection, so a Redis cache would empty database 1 while the planted
# queue sits in database 0. Had this recipe pointed both at one database, the
# flush would have wiped `queues:*` too and left the cell green for an effect
# `horizon:clear` did not cause.
#
# `CACHE_DRIVER` (the Laravel 10 name of the same key) and `SESSION_DRIVER` are
# deliberately left unset. One contrary cache key is enough to prove the
# mechanism. `CACHE_DRIVER` is read as an imposed key the recipe never wrote,
# which only the step can have put there. `SESSION_DRIVER` is read as `file`,
# which says the profile's value is present and nothing more: for one key in one
# run a default and an imposition write the same line, so that it is a default
# is checked statically against the profile in `test_feature_registry.py`, and
# the mechanism is proven by `QUEUE_CONNECTION` above, where an imposed key
# would have produced `sync`.
#
# The four URL keys (`REDIS_URL`, `DB_URL`, `DATABASE_URL`, `MAIL_URL`) are
# appended further down, after the install's artisan calls, with
# production-like values. The profile imposes the first three as empty and the
# env test reads them back empty, because a non-empty URL is `array_merge`d
# over every discrete host, port, user and password key beside it and would
# send a synced project to production's Redis or database whatever those pins
# say. The first three cannot sit in this file: `migrate` below would read
# `DB_URL` and connect to the remote host. `MAIL_URL` is appended with them
# because it belongs to the same family, not because anything above reads it -
# and it is imposed as a local DSN rather than emptied, because the guard that
# reads it is `isset` and an empty value would leave the mail transport null.
# The env test reads it back as that DSN.
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
CACHE_STORE=redis
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

# Production-like and unmistakably remote (`.invalid` never resolves), so a
# reader of the kept tree sees what the sync neutralised. Appended only now:
# every artisan call above that touches a database has already run. The
# MAIL_URL is the shape a real production .env carries - an API key as the
# username against a hosted relay - and no mail is sent here either way.
cat >> .env <<'ENV'
REDIS_URL=redis://:s3cret@redis.production.invalid:6379
DB_URL=mysql://acme:s3cret@db.production.invalid:3306/acme_prod
DATABASE_URL=mysql://acme:s3cret@db.production.invalid:3306/acme_prod
MAIL_URL=smtp://apikey:s3cret@smtp.production.invalid:587
ENV

composer show laravel/framework | grep -E '^(name|versions) '
composer show laravel/horizon | grep -E '^(name|versions) '
composer show predis/predis | grep -E '^(name|versions) '
php artisan --version
php -r 'var_dump(extension_loaded("pcntl"));'
php -v | head -1
composer --version
