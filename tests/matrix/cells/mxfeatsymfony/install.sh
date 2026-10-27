# A real Symfony with Doctrine and FOSElasticaBundle against a real
# Elasticsearch. Built like `mxsymfony` - same skeleton, same explicit
# `app_user` migration, same deliberately unapplied second migration - because
# this cell runs the symfony profile and the profile-keyed assertions run for
# it too. What it adds is the one feature, `symfony-search`.
composer create-project --no-interaction --prefer-dist symfony/skeleton .
composer require --no-interaction symfony/orm-pack

# Exported rather than written to `.env.local`, exactly as `mxsymfony` does:
# the stock `.env` stays untouched so the profile's env step has to patch that
# very file. Symfony's Dotenv never overrides a variable already in the
# process environment.
export DATABASE_URL='mysql://db:db@ddev-mxfeatsymfony-db:3306/db?serverVersion=mariadb-11.0.0'

mkdir -p migrations
# doctrine/doctrine-migrations-bundle 3.7's recipe registers `App\Migrations`
# in `src/Migrations`, where this recipe's `DoctrineMigrations` classes in
# `migrations/` are not found ("there are no registered migrations"). The
# files below are what a project that predates that recipe has, so the path
# is pinned to them rather than the files moved to follow the recipe.
cat > config/packages/doctrine_migrations.yaml <<'YAML'
doctrine_migrations:
    migrations_paths:
        'DoctrineMigrations': '%kernel.project_dir%/migrations'
YAML
# Suspected, not proven: the container the console compiled before this file
# existed goes stale when the file lands within the same second as that cache
# (mtime granularity), and the migrate below then saw no migrations in some
# runs (intermittent). The cache is removed to be safe; the cause was never
# isolated.
rm -rf var/cache
cat > migrations/Version20260101000000.php <<'PHP'
<?php

declare(strict_types=1);

namespace DoctrineMigrations;

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

final class Version20260101000000 extends AbstractMigration
{
    public function up(Schema $schema): void
    {
        $this->addSql('CREATE TABLE app_user (
            id INT AUTO_INCREMENT NOT NULL,
            email VARCHAR(180) NOT NULL,
            roles JSON NOT NULL,
            password VARCHAR(255) NOT NULL,
            PRIMARY KEY(id)
        ) DEFAULT CHARACTER SET utf8mb4 ENGINE = InnoDB');
        $this->addSql('CREATE TABLE doc (
            id INT NOT NULL,
            title VARCHAR(255) NOT NULL,
            PRIMARY KEY(id)
        ) DEFAULT CHARACTER SET utf8mb4 ENGINE = InnoDB');
    }

    public function down(Schema $schema): void
    {
        $this->addSql('DROP TABLE app_user');
        $this->addSql('DROP TABLE doc');
    }
}
PHP

php bin/console doctrine:migrations:migrate --no-interaction

# The same deliberately unapplied migration `mxsymfony` leaves, written after
# the migrate above so the dump that seeds the remote lacks its table.
cat > migrations/Version20260102000000.php <<'PHP'
<?php

declare(strict_types=1);

namespace DoctrineMigrations;

use Doctrine\DBAL\Schema\Schema;
use Doctrine\Migrations\AbstractMigration;

final class Version20260102000000 extends AbstractMigration
{
    public function up(Schema $schema): void
    {
        $this->addSql('CREATE TABLE matrix_marker (id INT NOT NULL, PRIMARY KEY(id))');
    }

    public function down(Schema $schema): void
    {
        $this->addSql('DROP TABLE matrix_marker');
    }
}
PHP

composer require --no-interaction friendsofsymfony/elastica-bundle

# The bundle does not register itself: measured, `composer require` leaves
# config/bundles.php holding only FrameworkBundle, DoctrineBundle and
# DoctrineMigrationsBundle. Without this entry Symfony does not know the
# `fos_elastica` namespace, and every console call - cache:clear,
# doctrine:migrations:migrate, populate - dies on the config file instead.
php -r '
$f = "config/bundles.php";
$s = file_get_contents($f);
$add = "    FOS\\ElasticaBundle\\FOSElasticaBundle::class => [\"all\" => true],\n";
file_put_contents($f, preg_replace("/\n\];\s*$/", "\n" . $add . "];\n", $s));
'
grep -q FOSElasticaBundle config/bundles.php || {
  echo "install.sh failed to register FOSElasticaBundle; every console call would die on config" >&2
  exit 1
}

# The configuration and the indexed entity, from the cell's own recipe files.
# `conftest.py::_install` stages them at /tmp/matrix-app inside the container
# rather than in the project, because `composer create-project .` above
# refuses a project directory that holds anything at all - see the comment on
# the staging itself.
mkdir -p src/Entity
cp /tmp/matrix-app/fos_elastica.yaml config/packages/fos_elastica.yaml
cp /tmp/matrix-app/Doc.php src/Entity/Doc.php

# Proof the configuration loads at all, before anything depends on it: a bad
# `fos_elastica` block fails here with the option named, rather than three
# steps later with the same message attached to an unrelated command.
php bin/console cache:clear --no-interaction

# A local row the import has to replace - not what carries the schema. The
# `doc` table reaches the remote because `Version20260101000000` created it and
# `conftest.py::_seed` dumps `--no-data`, which emits `CREATE TABLE doc`
# whether a row was ever inserted or not. What this row adds is a readable
# discrepancy: the documents the sync indexes come from seed.sql (ids 11 to
# 13), so a `doc` table that still held this one would read as four documents
# where the cell's seed accounts for three. Nothing asserts that today -
# `IndexMarker.minimum_after` is a floor and
# `test_the_feature_command_populated_the_index` checks `>=`, so a count of
# four passes. The row makes a stale import legible, it does not catch one.
php bin/console dbal:run-sql "INSERT INTO doc (id, title) VALUES (1,'alpha-local')"

# The index must not exist before the sync: `IndexMarker.expected_before` is 0
# and `es_count` reads an absent index as 0. Measured: the bundle creates
# nothing at install time and `populate` resets the index itself, so there is
# normally nothing here to remove - this is a precaution, not a removal of
# anything known to be there.
curl -s -X DELETE "http://ddev-mxfeatsymfony-es:9200/app" >/dev/null || true

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment (PHP, composer, framework, bundle, Elastica client) the
# row in VERIFIED.md will claim to have been verified against.
#
# The tail is `result.stdout[-600:]` (conftest.py::_install), and this cell has
# nearly filled it: measured on the verification run, the block from the first
# `name     :` line to the end is 347 of those 600 characters, so roughly 250
# are left. That is three or four more `composer show` pairs - after which the
# oldest version line silently falls off the top of what Task 7 reads. Keep
# this block last, and keep it short.
composer show symfony/framework-bundle | grep -E '^(name|versions) '
composer show doctrine/orm | grep -E '^(name|versions) '
composer show friendsofsymfony/elastica-bundle | grep -E '^(name|versions) '
composer show ruflin/elastica | grep -E '^(name|versions) '
php bin/console --version
php -v | head -1
composer --version
