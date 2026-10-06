# A real TYPO3 13, with its schema created by its own CLI. `:^13` is
# explicit because composer's default for this package is the newest major,
# which is 14 - the unconstrained command would install a different
# framework than the profile variant this cell is named for.
composer create-project --no-interaction --prefer-dist 'typo3/cms-base-distribution:^13' .

# TYPO3's non-interactive installer reads these instead of prompting.
TYPO3_DB_DRIVER=mysqli \
TYPO3_DB_USERNAME=db \
TYPO3_DB_PASSWORD=db \
TYPO3_DB_PORT=3306 \
TYPO3_DB_HOST=ddev-mxtypo313-db \
TYPO3_DB_DBNAME=db \
TYPO3_SETUP_ADMIN_EMAIL=admin@example.invalid \
TYPO3_SETUP_ADMIN_USERNAME=installer \
TYPO3_SETUP_ADMIN_PASSWORD=Password.1234 \
TYPO3_PROJECT_NAME=matrix \
TYPO3_SERVER_TYPE=other \
  vendor/bin/typo3 setup --force --no-interaction

# A column the core schema expects, removed after the installer created it. It
# is therefore missing from the dump that seeds the remote, hence from the
# local database after the import, and only a schema update against the
# project's own definitions - what the profile's `extension:setup` setup step
# is for - can bring it back.
# `--skip-ssl`: local-db stands in for DDEV's server, which configures no
# certificate, while this image's client requires TLS by default.
mariadb --skip-ssl --host=ddev-mxtypo313-db --user=db --password=db db \
  -e 'ALTER TABLE tt_content DROP COLUMN header_link'

composer show typo3/cms-core | grep -E '^(name|versions) '
php -v | head -1
composer --version
