# A real Symfony with Doctrine. The user table is created by an explicit
# migration rather than by MakerBundle's interactive generator, because this
# cell needs a table name that is deliberately NOT the profile's default
# (`user`): a resolution that silently fell back to the default must fail here.
composer create-project --no-interaction --prefer-dist symfony/skeleton .
composer require --no-interaction symfony/orm-pack

# Exported instead of written to `.env.local`: the stock `.env` that the
# skeleton and the Doctrine recipe ship stays untouched, so the profile's env
# step has to patch that very file - the layout most projects start from.
# Symfony's Dotenv never overrides a variable that is already in the process
# environment, so this applies to every console call below and to nothing else.
export DATABASE_URL='mysql://db:db@ddev-mxsymfony-db:3306/db?serverVersion=mariadb-11.0.0'

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
    }

    public function down(Schema $schema): void
    {
        $this->addSql('DROP TABLE app_user');
    }
}
PHP

php bin/console doctrine:migrations:migrate --no-interaction

# A migration that exists in the project but has run nowhere, as it would on a
# branch ahead of production. It is written only now, after the migrate above,
# so the dump taken from the local database lacks its table, and the only
# thing that can create it after the sync is the profile's own
# `doctrine:migrations:migrate` - which is `allow_failure`, so its effect is
# the only evidence it ran.
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

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment (PHP, composer, framework) the row in VERIFIED.md will
# claim to have been verified against.
composer show symfony/framework-bundle | grep -E '^(name|versions) '
composer show doctrine/orm | grep -E '^(name|versions) '
composer show symfony/skeleton 2>/dev/null | grep -E '^(name|versions) ' || true
php bin/console --version
php -v | head -1
composer --version
