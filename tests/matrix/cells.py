"""The variants this matrix verifies, and what "verified" means for each.

One registry, so the workflow, the tests and `VERIFIED.md` cannot disagree
about what is covered. Everything a cell asserts is declared here rather than
written into a test body: adding a cell is then a registry entry plus three
recipe files, not a new test.
"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class FlushMarker:
    """A cache row a cell plants so its `flush_cache` rule can be seen working.

    The engine discards the result of a `flush_cache` command on purpose, so a
    command that does not exist never fails a sync and no exit status says it
    ran. The effect does: `insert` puts a row into a cache table that no other
    sanitize rule touches, `where` selects exactly that row in `table`, and the
    rule must have removed it once the sync is over.
    """

    table: str
    insert: str
    where: str


@dataclass(frozen=True)
class RedisMarker:
    """A queue entry a cell plants so a feature's Redis command can be seen working.

    `key` is the Redis key the entry lands under, prefix included: Laravel
    builds `queues:<name>` in `Illuminate\\Queue\\RedisQueue`, but
    `laravel/laravel`'s `config/database.php` prefixes every Redis key with
    `Str::slug(APP_NAME) . '-database-'`, so the full key is not the one the
    queue name suggests. `push` is a command run in the web container that
    plants exactly one entry, and `expected_before` is how many the test must
    see before the sync - a marker that was never planted would otherwise let
    "the queue is empty afterwards" pass without the feature doing anything.
    """

    key: str
    push: str
    expected_before: int = 1


@dataclass(frozen=True)
class IndexMarker:
    """What a cell expects of a search index before and after the sync.

    `expected_before` is 0 because an index the sync is about to build is
    either absent or empty beforehand - the mirror of `RedisMarker`, whose
    planted entry must be present. `minimum_after` is a floor rather than an
    exact count: the document count follows from the cell's seed, and pinning
    it here would break the marker whenever the seed grows.
    """

    index: str
    expected_before: int = 0
    minimum_after: int = 1


@dataclass(frozen=True)
class FeatureCheck:
    """What a cell proves about one feature.

    `feature` is the name `features/<name>.yaml` declares, and naming it is
    itself an assertion: the run must report that feature as active. A check
    with nothing else set therefore still proves something, which is exactly
    `inertia`'s case - it declares no commands at all.

    "Active", not "detected": `typo3-processedfiles` has no detect block by
    design (see the comment at the top of its file), so it is activated by a
    `features:` key instead. `cli/render.py::render_features` prints both
    kinds in one block, which is what the assertion reads.

    `appears` are paths that must exist after the sync and must have been
    removed by the install recipe before it - otherwise the assertion passes
    against a path the install left behind and proves nothing. `disappears`
    are paths that must be gone after the sync. `row_marker` reuses
    `FlushMarker`: a row the cell plants which the sync must have removed.
    `redis_marker` and `index_marker` are its equivalents for a feature whose
    effect lands in Redis or in a search index rather than in SQL.
    """

    feature: str
    appears: tuple[str, ...] = ()
    disappears: tuple[str, ...] = ()
    row_marker: FlushMarker | None = None
    redis_marker: RedisMarker | None = None
    index_marker: IndexMarker | None = None


@dataclass(frozen=True)
class EnvMarker:
    """One key the project's `.env` must carry after a sync, and who wrote it.

    Four kinds, because the env step makes four different promises:

    - `imposed`: the profile's `env.set` carries the key, so the value must be
      the profile's whatever the project said. Only a recipe that declares a
      contrary value (`contrary`) makes this a measured overwrite; without one
      the key merely reads right, which an absent key filled by a default
      would do too.
    - `kept`: the project declared the key itself and the profile only
      `env.default`s it, so the project's value must survive the sync. This is
      the kind that proves the default mechanism: an imposed key would have
      overwritten the recipe's value.
    - `defaulted`: the project declared nothing, and the profile's default
      value is present. For a single key in a single run this is
      indistinguishable from `imposed` - a key in `env.set` writes the same
      line - so the run cannot show that the key sits in `env.default`.
      `test_feature_registry.py` checks that against the profile statically;
      the mechanism itself is proven by a `kept` marker.

    - `overridden`: the project's own `ddev-sync.yaml` names the key in
      `env.set`, so its value wins over the profile's, while the profile's
      other imposed keys still land. That last clause is the property under
      test - `merge_layers` merges the `env` mapping per key - so this marker
      is only meaningful **together with** the cell's `imposed` markers: on
      its own it would also pass if the profile had simply stopped writing the
      key. `test_feature_registry.py` checks that the key is in both the
      project's `env.set` and the profile's, with different values.

    `contrary` is the value the cell's recipe writes before the sync, for an
    `imposed` key whose imposition the cell means to measure. It must differ
    from `expected` and `test_feature_registry.py` checks the recipe really
    declares it, so deleting that line cannot silently turn the marker back
    into a control.
    """

    key: str
    expected: str
    kind: Literal["imposed", "kept", "defaulted", "overridden"]
    contrary: str | None = None


@dataclass(frozen=True)
class Cell:
    """One profile variant, installed for real and synced end to end.

    `required_tables` lists only the tables the profile's **non-tolerated**
    `requires_table` rules declare. A rule carrying `tolerate_missing_table`
    is deliberately absent: asserting it would turn an intended tolerance into
    a false failure.

    `truncated_tables` lists the tables a profile's truncate rules empty,
    **including** the ones marked `tolerate_missing_table`: the tolerance only
    decides what happens when the table is absent, and a cell whose install
    creates the table must still see it emptied. Defaulted so a profile with
    no such rules states nothing.

    `password_column` is the (table, column) a `reset_passwords` rule
    rewrites, `admin_user` the login a `create_admin` rule creates, and
    `production_url` the URL a sanitize rule must remove. `None` means the
    profile has no such rule - which is itself part of what the cell states.

    `identity_column` and `seeded_users` say which rows of the password
    table the cell's seed put there: the values of a column no sanitize rule
    rewrites (a login name, not an email address). They exist because
    `create_admin` runs after `reset_passwords` and inserts a row whose hash
    is genuinely not the reset value, so "every row was reset" cannot be
    asserted - "every *seeded* row was reset" can, and still fails when the
    reset never ran. The admin is found through the same column.

    `flush_marker` is the row the cell's `flush_cache` rule must remove. `None`
    means the profile has no such rule, or that nothing observable lives in the
    database for it to clear.

    `remote_packages` is what the stand-in production host installs at start
    (`|`-separated, the format of the sshd image's `INSTALL_PACKAGES`). The
    default is what every sync needs - a mysqldump client and rsync. A profile
    whose commands run on production itself needs more: WordPress's `wp_cli`
    credentials source runs `wp` over ssh, so its cell adds PHP. It is a field,
    not a shared default, because the packages are installed from the network
    at every start and the other cells should not pay for them.

    `features` is what this cell proves about the *features* the sync
    activated, as opposed to the profile - see `FeatureCheck`. Defaulted, so
    a cell that declares no check states nothing about features and keeps
    asserting exactly what it asserted before this field existed.

    `services` names the compose profiles this cell needs beyond the four
    services every cell starts. Each new service sits behind a `profiles:`
    entry of its own name, so one string selects the profile and names the
    compose service to exec into. Defaulted, so a cell that names nothing
    starts exactly what it started before this field existed - which matters,
    because Elasticsearch is 512 MB of heap that only the cell declaring it
    has any use for. Stated that way rather than as a count of the cells that
    do without it: this field's first wording said "six of them", which the
    very change that added the field made wrong.
    """

    # `mx` + letters and digits only, for three independent reasons:
    # - `name` becomes the DDEV project name in `.ddev/config.yaml`, from which
    #   the engine derives `ddev-<name>-web`; a plain `laravel` would claim
    #   the container name of a developer's real project, and the engine's
    #   `docker exec` could then run `composer`/`artisan` in *their* project.
    #   The prefix keeps the cell out of that namespace.
    # - no hyphen: `pytest -k typo3-13` selects nothing, because `-k` parses
    #   its argument as an expression and the hyphen is not a substring match.
    # - no underscore: the name ends up in the network alias `ddev-<name>-db`,
    #   which the engine passes to the mysql client as a hostname.
    #
    # One further constraint, on the set of names rather than on the charset:
    # no name may contain another cell's name. `.github/workflows/matrix.yaml`
    # runs one job per cell with `-k "$CELL"`, and `-k` matches a substring of
    # the test id - `-k mxtypo312` collects one cell's 25 tests while
    # `-k mxtypo31` collects 50, from `mxtypo312` and `mxtypo313`
    # both (`mxfeattypo3`, the third TYPO3 cell, does not carry that
    # substring, which is the point). A feature cell called
    # `mxlaravelfeat` would therefore have been swept into the `mxlaravel`
    # job, which emits the stock-skeleton `laravel` row; hence `mxfeat*`.
    name: str
    profile: str
    project_type: str
    docroot: str
    php_image: str
    required_tables: tuple[str, ...]
    admin_user: str | None
    password_column: tuple[str, str] | None
    production_url: str | None
    truncated_tables: tuple[str, ...] = ()
    identity_column: str | None = None
    seeded_users: tuple[str, ...] = ()
    flush_marker: FlushMarker | None = None
    remote_packages: str = "mariadb-client|rsync"
    features: tuple[FeatureCheck, ...] = ()
    services: tuple[str, ...] = ()
    env_markers: tuple[EnvMarker, ...] = ()


# Every TYPO3 cell keeps the same cache tables, so they all plant the same row.
TYPO3_FLUSH_MARKER = FlushMarker(
    table="cache_hash",
    # `expires` far in the future, so an expiry sweep cannot be what removes the
    # row; only `cache:flush` does.
    insert=(
        "INSERT INTO cache_hash (identifier, expires, content) "
        "VALUES ('matrix_marker', 2000000000, 'present')"
    ),
    where="identifier = 'matrix_marker'",
)

# `name` is both the directory under `cells/` and the pytest parameter id, so
# a failure names the variant directly.
CELLS: tuple[Cell, ...] = (
    Cell(
        name="mxlaravel",
        profile="laravel",
        project_type="laravel",
        docroot="public",
        # PHP 8.3.35 was printed by `php -v` in this image, and the newest
        # laravel/laravel release (v13.10.1) declares `php ^8.3`.
        php_image="php:8.3-cli",
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        # Not `email`: the profile's sanitize rules rewrite it.
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
    ),
    Cell(
        name="mxfeatlaravel",
        # A second laravel cell, not a change to `mxlaravel`: that cell's row
        # says "against a fresh laravel/laravel skeleton, profile unchanged",
        # and installing Filament and Livewire into it would make the sentence
        # false to save three minutes of runtime.
        profile="laravel",
        project_type="laravel",
        docroot="public",
        php_image="php:8.3-cli",
        # The profile-shaped fields are `mxlaravel`'s, verbatim: `Cell`'s
        # docstring defines `None`/`()` as "the profile has no such rule", so
        # emptying them here would make that meaning false and print a lying
        # skip reason. The duplicated profile evidence costs no extra sync (the
        # same one runs either way) and is not what this cell exists to record:
        # the rows it is here to fill are VERIFIED.md's *feature* rows, one
        # per `FeatureCheck` below. `tools/apply_verified_rows.py` keys a
        # feature cell's rows by feature name and emits no profile row for
        # it, so applying them cannot reach `mxlaravel`'s row - which
        # `tests/test_apply_verified_rows.py` holds to account, on this cell
        # by name because it is the one whose profile row a regression would
        # overwrite.
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
        features=(
            FeatureCheck(
                feature="node-build",
                # The directory, not a manifest path: where inside it the
                # manifest lands is the toolchain's choice, so a pinned
                # filename would break on a version bump without telling
                # anyone anything true. This build wrote
                # `public/build/manifest.json` and no `public/build/.vite/`
                # at all (vite 8.3.4, laravel-vite-plugin 3.2.0).
                appears=("public/build",),
            ),
            FeatureCheck(
                feature="filament",
                # `filament:optimize`'s two steps, "Caching components" and
                # "Caching Blade icons", and the two paths that appear -
                # measured against filament/filament v5.10.1.
                # `filament:upgrade` is deliberately absent: it reported
                # success and left no new path, but the probe had not removed
                # its likely targets under `public/`, so their absence proves
                # nothing. See the VERIFIED.md row.
                appears=(
                    "bootstrap/cache/filament/panels",
                    "bootstrap/cache/blade-icons.php",
                ),
            ),
            FeatureCheck(
                feature="livewire",
                # `livewire:publish --assets` copies
                # vendor/livewire/livewire/dist into here - eleven files,
                # the whole of that directory, measured against
                # livewire/livewire v4.4.7.
                appears=("public/vendor/livewire",),
            ),
            # Detection only. The feature declares no commands at all
            # ("Detected for reporting; the build itself is node-build's
            # job"), so that it is reported is the whole of its claim.
            FeatureCheck(feature="inertia"),
        ),
    ),
    Cell(
        name="mxfeathorizon",
        # A third Laravel cell rather than Horizon added to `mxfeatlaravel`:
        # that cell's four rows state the environment they were measured
        # against, and adding Horizon and Redis makes that statement stale
        # until it re-runs and all four are rewritten. A cell of its own also
        # keeps Redis out of their runs, which is what `services` is for.
        profile="laravel",
        project_type="laravel",
        docroot="public",
        php_image="php:8.3-cli",
        services=("redis",),
        env_markers=(
            # The reason this cell needs no queue override: `install.sh` writes
            # QUEUE_CONNECTION=redis, the profile only defaults it, so the
            # project's value has to survive the sync. The cell's first run
            # lost its planted job to the old behaviour, which set `sync`.
            EnvMarker(key="QUEUE_CONNECTION", expected="redis", kind="kept"),
            # Declared by nothing in the recipe, so the profile's value must
            # be there. This does not show that the key is a default rather
            # than imposed - the run writes the same line either way; the
            # registry test checks `env.default` against the profile, and
            # QUEUE_CONNECTION above is what proves the mechanism.
            EnvMarker(key="SESSION_DRIVER", expected="file", kind="defaulted"),
            # CACHE_STORE is imposed, and this is the measured case: the recipe
            # writes `CACHE_STORE=redis`, a contrary value chosen on purpose,
            # so reading `file` after the sync is an overwrite the step made
            # rather than an absence it filled. The sync's own `optimize:clear`
            # flushes whatever cache store is configured, which for a remote
            # store is somebody else's data; a regression that defaulted the
            # key would keep `redis` here and turn this red. CACHE_DRIVER is
            # the same key under its Laravel 10 name and is left undeclared by
            # the recipe, so it is an appended imposed key, not a second proof
            # of overwriting.
            EnvMarker(key="CACHE_STORE", expected="file", kind="imposed", contrary="redis"),
            EnvMarker(key="CACHE_DRIVER", expected="file", kind="imposed"),
            # The three URL keys, which the recipe fills with production-like
            # values and the profile must blank. A non-empty URL is
            # `array_merge`d over every discrete host, port, user and password
            # key beside it, so an unblanked one would point this synced
            # project at production's Redis or database whatever the pins say.
            # Empty is the value that makes them inert. They are written after
            # the install's own artisan calls (see `install.sh`), which would
            # otherwise connect to the remote host.
            EnvMarker(
                key="REDIS_URL",
                expected="",
                kind="imposed",
                contrary="redis://:s3cret@redis.production.invalid:6379",
            ),
            EnvMarker(
                key="DB_URL",
                expected="",
                kind="imposed",
                contrary="mysql://acme:s3cret@db.production.invalid:3306/acme_prod",
            ),
            EnvMarker(
                key="DATABASE_URL",
                expected="",
                kind="imposed",
                contrary="mysql://acme:s3cret@db.production.invalid:3306/acme_prod",
            ),
            # The fourth URL family, measured the same way and with the one
            # value that is not empty: `MailManager::getConfig` guards on
            # `isset`, so an empty MAIL_URL still enters the parse branch and
            # leaves `transport` null. A local DSN is what neutralises the
            # production relay the recipe writes, and reading it back is what
            # says the three MAIL_* pins are not decorative.
            EnvMarker(
                key="MAIL_URL",
                expected="smtp://localhost:1025",
                kind="imposed",
                contrary="smtp://apikey:s3cret@smtp.production.invalid:587",
            ),
            # The project's own `env: set:` names MAIL_PORT, which the profile
            # also imposes (as 1025), so the project's value must win - and
            # MAIL_HOST, which the profile imposes and the project does not
            # name, must still read the profile's value. The pair is the
            # evidence that the `env` mapping merges per key; MAIL_PORT alone
            # would also pass if the profile had stopped writing it. Nothing
            # in this cell sends mail, so the key cannot touch anything else
            # it asserts. The claim is about the line in the file and not about
            # where mail goes: the imposed MAIL_URL above carries its own port,
            # which Laravel merges over this one, so a project wanting another
            # local port overrides both keys. That is stated in the profile.
            EnvMarker(key="MAIL_PORT", expected="2525", kind="overridden"),
            EnvMarker(key="MAIL_HOST", expected="localhost", kind="imposed"),
            # Imposed, but not evidence that the step ran: the recipe writes
            # these same two hosts, so a step that never executed would leave
            # them reading correctly. The keys above are what rule that out,
            # since the recipe declares each of them with another value or not
            # at all. These two check that the pin resolves `${DDEV_SITENAME}`
            # to the cell's name.
            EnvMarker(key="DB_HOST", expected="ddev-mxfeathorizon-db", kind="imposed"),
            EnvMarker(key="REDIS_HOST", expected="ddev-mxfeathorizon-redis", kind="imposed"),
        ),
        # `mxlaravel`'s profile-shaped values, verbatim: `Cell`'s docstring
        # defines None/() as "the profile has no such rule", so emptying them
        # would make that meaning false and print a lying skip reason.
        required_tables=("users",),
        admin_user=None,
        password_column=("users", "password"),
        production_url=None,
        truncated_tables=("sessions", "jobs", "failed_jobs"),
        identity_column="name",
        seeded_users=("Alice Hofer", "Bruno Kalb"),
        features=(
            FeatureCheck(
                feature="horizon",
                redis_marker=RedisMarker(
                    # The prefix is Laravel's, not this cell's invention:
                    # config/database.php sets the Redis prefix to
                    # `Str::slug((string) env('APP_NAME', 'laravel'))` plus
                    # `-database-`, and no .env here sets APP_NAME, so the
                    # default `laravel` is slugged.
                    # Illuminate\Queue\RedisQueue then builds `queues:default`
                    # under it - `default` being
                    # `queue.connections.redis.queue`, which is also the queue
                    # `horizon:clear` resolves with no `--queue` option. A
                    # marker naming the unprefixed key would find nothing at
                    # plant time.
                    key="laravel-database-queues:default",
                    # A dispatched job **class**, not a closure: a queued
                    # closure is serialized by reading the file it was written
                    # in, and `tinker --execute=` evals its argument, so a
                    # dispatched closure dies with
                    # "file_get_contents(eval()'d code)" and never reaches
                    # Redis (measured against laravel/framework v13.35.0).
                    # The install recipe generates the class for that reason,
                    # and its `handle()` is empty: what is asserted is that one
                    # job sits in the queue Horizon is about to clear, not what
                    # the job would have done.
                    #
                    # A plain `dispatch` reaches Redis only because the sync
                    # leaves the recipe's `QUEUE_CONNECTION=redis` alone: the
                    # profile defaults that key rather than imposing it, and
                    # `env_markers` above asserts it survived. The feature's
                    # own command does not depend on that: `horizon:clear`
                    # takes its connection from
                    # the first entry of `horizon.defaults`, which is `redis`
                    # (vendor/laravel/horizon/src/Console/ClearCommand.php),
                    # not from `queue.default`.
                    push="php artisan tinker --execute='dispatch(new App\\Jobs\\MatrixMarkerJob);'",
                ),
            ),
        ),
    ),
    Cell(
        name="mxfeatsymfony",
        # The first feature cell for symfony; `mxsymfony` stays a stock
        # skeleton plus orm-pack, which is what its row says it is.
        profile="symfony",
        project_type="symfony",
        docroot="public",
        php_image="php:8.3-cli",
        services=("elasticsearch",),
        # `mxsymfony`'s profile-shaped values, verbatim - `Cell`'s docstring
        # defines None/() as "the profile has no such rule".
        required_tables=("app_user",),
        admin_user=None,
        password_column=("app_user", "password"),
        production_url=None,
        identity_column="id",
        seeded_users=("1", "2"),
        features=(
            FeatureCheck(
                feature="symfony-search",
                index_marker=IndexMarker(
                    # The configured name, verbatim. Measured against bundle
                    # v7.2.0: `_cat/indices` reports `app` and `_cat/aliases`
                    # is empty - no date suffix and no alias, because that is
                    # `use_alias`'s behaviour and it defaults to off.
                    index="app",
                    # Three rows in seed.sql, so three documents. A floor
                    # rather than an exact count, so a growing seed does not
                    # break the marker.
                    minimum_after=3,
                ),
            ),
        ),
    ),
    Cell(
        name="mxtypo313",
        profile="typo3@13",
        project_type="typo3",
        docroot="public",
        php_image="php:8.3-cli",
        # The three non-tolerated requires_table rules in profiles/typo3.yaml.
        # `tx_scheduler_task` and the fe_* tables are deliberately absent:
        # they carry tolerate_missing_table because EXT:scheduler and
        # EXT:frontend are optional, and asserting them here would turn an
        # intended tolerance into a false failure.
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        # `tx_scheduler_task` is not listed: the base distribution does not
        # install EXT:scheduler, so the table does not exist here. `fe_sessions`
        # does (EXT:frontend ships in the distribution) and must be emptied.
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
    ),
    Cell(
        name="mxfeattypo3",
        # A second typo3@13 cell for the same reason `mxfeatlaravel` is a
        # second laravel cell: the typo3@13 row rests on a stock install, and
        # this one is not stock.
        profile="typo3@13",
        project_type="typo3",
        docroot="public",
        php_image="php:8.3-cli",
        # `mxtypo313`'s profile-shaped fields, verbatim - see the note on
        # `mxfeatlaravel` for why they are not emptied.
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
        features=(
            FeatureCheck(
                feature="typo3-processedfiles",
                # The feature contributes both a sanitize rule and a setup
                # command, so both get evidence.
                #
                # The rule is `TRUNCATE TABLE sys_file_processedfile` with no
                # `tolerate_missing_table`, and that is correct: the table is
                # defined in typo3/cms-core's own ext_tables.sql - line 123 of
                # vendor/typo3/cms-core/ext_tables.sql in the v13.4.35 this
                # cell installed - so a core install always has it. Every
                # column of the table the run actually created is
                # auto-increment (`uid`), nullable (`name`, `processing_url`,
                # `configuration`) or carries a DEFAULT; read off
                # `SHOW CREATE TABLE` rather than off that .sql file, since
                # TYPO3's schema migrator is what turns the one into the
                # other. That is why this two-column INSERT is valid under the
                # local database's strict mode (`@@sql_mode` was
                # STRICT_TRANS_TABLES,ERROR_FOR_DIVISION_BY_ZERO,
                # NO_AUTO_CREATE_USER,NO_ENGINE_SUBSTITUTION).
                row_marker=FlushMarker(
                    table="sys_file_processedfile",
                    insert=(
                        "INSERT INTO sys_file_processedfile (identifier, task_type) "
                        "VALUES ('matrix_marker', 'Image.CropScaleMask')"
                    ),
                    where="identifier = 'matrix_marker'",
                ),
                # The setup command is
                # `rm -rf ${DDEV_DOCROOT}/fileadmin/_processed_`, and this is
                # the only shipped feature that interpolates a placeholder
                # into an `rm -rf` - the reason
                # `operations/placeholders.py::_validated_docroot` exists.
                # Until this cell, no run had exercised it.
                disappears=("public/fileadmin/_processed_",),
            ),
        ),
    ),
    Cell(
        name="mxtypo312",
        profile="typo3@12",
        project_type="typo3",
        docroot="public",
        php_image="php:8.2-cli",
        required_tables=("be_sessions", "sys_log", "be_users"),
        admin_user="ddev",
        password_column=("be_users", "password"),
        production_url=None,
        truncated_tables=("be_sessions", "sys_log", "fe_sessions"),
        identity_column="username",
        seeded_users=("editor.hofer", "editor.kalb"),
        flush_marker=TYPO3_FLUSH_MARKER,
    ),
    Cell(
        name="mxsymfony",
        profile="symfony",
        project_type="symfony",
        docroot="public",
        php_image="php:8.3-cli",
        # The resolved name, not `${SYMFONY_USER_TABLE}`: the cell's config
        # sets `database.symfony_user_table` to the table the install really
        # creates, and the assertion proves resolution agreed with it.
        required_tables=("app_user",),
        admin_user=None,
        password_column=("app_user", "password"),
        production_url=None,
        # No `truncated_tables`: the profile's only truncate rule empties
        # `messenger_messages`, which a stock `symfony/orm-pack` installation
        # does not have - Doctrine Messenger creates it lazily on first use,
        # and Messenger is not even among the pack's packages. So the rule's
        # `tolerate_missing_table` is what this cell exercises, and the table
        # cannot be asserted empty.
        # Not `email`: the profile's sanitize rules rewrite it. `id` is
        # preserved by the import, and a MakerBundle-shaped user table has no
        # login column to use instead.
        identity_column="id",
        seeded_users=("1", "2"),
    ),
    Cell(
        name="mxwordpress",
        profile="wordpress",
        project_type="wordpress",
        # DDEV writes `docroot: ""` for WordPress: the project root is the web
        # root, so wp-cli runs there.
        docroot="",
        php_image="php:8.3-cli",
        # The resolved name, not `${WP_TABLE_PREFIX}`. The install chooses
        # `mxwp_` rather than WordPress's default `wp_`; the engine reads that
        # prefix from production's wp-config.php (cells/mxwordpress/remote-only/)
        # over ssh, because the cell's config keeps the profile's `wp_cli`
        # credentials source and sets no `database.wp_table_prefix`.
        required_tables=("mxwp_users",),
        admin_user="ddev",
        password_column=("mxwp_users", "user_pass"),
        production_url="https://acme-production.example",
        # No `truncated_tables`: the profile has no truncate rule.
        # Not `user_email`: the profile's sanitize rules rewrite it.
        identity_column="user_login",
        seeded_users=("alice", "bruno"),
        # `wp config get` runs on production itself, so that host needs PHP; the
        # init script in `remote-init/` adds wp-cli on top of it.
        remote_packages="mariadb-client|rsync|php83|php83-phar|php83-mbstring|php83-openssl|php83-iconv|php83-ctype",
    ),
    Cell(
        name="mxdrupal",
        profile="drupal",
        project_type="drupal",
        docroot="web",
        php_image="php:8.3-cli",
        required_tables=("users_field_data",),
        admin_user="ddev",
        password_column=("users_field_data", "pass"),
        production_url=None,
        # No `truncated_tables`, although the profile has two truncate rules:
        # `sessions` does not exist after a stock Drupal 11.4.8 install (observed
        # with SHOW TABLES), so `clear-sessions` runs on its
        # `tolerate_missing_table`; and `watchdog` cannot be asserted empty,
        # because a setup command, which runs after the sanitize step, writes a
        # log row of its own ("Maintenance mode disabled."). Its rule is checked by
        # `test_drupal_clear_watchdog_removed_the_production_log` instead.
        # Not `mail`: the profile's sanitize rules rewrite it.
        identity_column="name",
        seeded_users=("alice", "bruno"),
        flush_marker=FlushMarker(
            table="cache_default",
            # `expire` -1 is Drupal's CACHE_PERMANENT, so only a flush removes it.
            insert=(
                "INSERT INTO cache_default "
                "(cid, data, expire, created, serialized, tags, checksum) "
                "VALUES ('matrix_marker', 'present', -1, 0, 0, '', 0)"
            ),
            where="cid = 'matrix_marker'",
        ),
    ),
)
