# A real Drupal, with its schema created by its own installer - including
# users_field_data, the table profiles/drupal.yaml declares. Unconstrained,
# because for this package composer's default is the newest *stable* release
# (the 12.0.0 pre-releases are not considered), which is what a new project
# gets today. drush comes from the project's dependencies, which is what the
# profile's commands assume (`vendor/bin/drush`).
composer create-project --no-interaction --prefer-dist drupal/recommended-project .
composer require --no-interaction drush/drush

vendor/bin/drush site:install standard \
  --db-url=mysql://db:db@ddev-mxdrupal-db:3306/db \
  --site-name=matrix \
  --account-name=installer \
  --account-pass=Password.1234 \
  --yes

# A database update that exists in the project but has run nowhere, as it would
# on a branch ahead of production. The module is installed first with only an
# update 10000, so Drupal records the schema version 10000 for it; update
# 10001 is added to the code afterwards. The dump taken from the local database
# therefore carries version 10000 and lacks the table the update creates, and
# the only thing that can bring it into existence after the sync is the
# profile's own `drush updatedb --yes` - which is `allow_failure`, so its effect
# is the only evidence it ran.
mkdir -p web/modules/custom/matrix_marker
cat > web/modules/custom/matrix_marker/matrix_marker.info.yml <<'YML'
name: Matrix marker
type: module
description: Carries one pending database update for the verification matrix.
core_version_requirement: ^11 || ^12
YML
cat > web/modules/custom/matrix_marker/matrix_marker.install <<'PHP'
<?php

/**
 * Already applied: the version this module is installed at.
 */
function matrix_marker_update_10000() {
}
PHP
vendor/bin/drush pm:install matrix_marker --yes
cat >> web/modules/custom/matrix_marker/matrix_marker.install <<'PHP'

/**
 * Not applied anywhere yet: creates the table the matrix looks for.
 */
function matrix_marker_update_10001() {
  \Drupal::database()->schema()->createTable('matrix_marker', [
    'fields' => ['id' => ['type' => 'int', 'not null' => TRUE]],
    'primary key' => ['id'],
  ]);
}
PHP

# Printed last, because conftest shows the tail of the output: these lines are
# the whole environment (PHP, composer, framework) the row in VERIFIED.md will
# claim to have been verified against.
composer show drupal/core | grep -E '^(name|versions) '
vendor/bin/drush --version
php -v | head -1
composer --version
