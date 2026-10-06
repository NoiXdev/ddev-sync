# A real TYPO3 12, with its schema created by its own CLI. `:^12` is
# explicit because composer's default for this package is the newest major,
# which is 14 - the unconstrained command would install a different
# framework than the profile variant this cell is named for.
#
# `--no-blocking` because TYPO3 12 is past its support window: composer 2.10.3
# refuses *every* 12.4 release of typo3/cms-core and typo3/cms-backend
# (v12.4.0 to v12.4.45) on security advisories, so without it the project
# cannot be resolved at all. A throwaway test installation of an affected
# release is exactly what this cell is for. Only the resolution needs it: the
# profile's own `composer install` setup step installs from the lock file
# this produces and is not blocked (checked by running it without any policy
# override), so nothing is persisted into the project's composer.json.
composer create-project --no-interaction --no-blocking --prefer-dist 'typo3/cms-base-distribution:^12' .

# TYPO3's non-interactive installer reads these instead of prompting.
TYPO3_DB_DRIVER=mysqli \
TYPO3_DB_USERNAME=db \
TYPO3_DB_PASSWORD=db \
TYPO3_DB_PORT=3306 \
TYPO3_DB_HOST=ddev-mxtypo312-db \
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
mariadb --skip-ssl --host=ddev-mxtypo312-db --user=db --password=db db \
  -e 'ALTER TABLE tt_content DROP COLUMN header_link'

composer show typo3/cms-core | grep -E '^(name|versions) '
php -v | head -1
composer --version
