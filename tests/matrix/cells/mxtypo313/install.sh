# A real TYPO3 13, with its schema created by its own CLI. `:^13` is
# explicit because composer's default for this package is the newest major,
# which is 14 - the unconstrained command would install a different
# framework than the profile variant this cell is named for.
#
# `--no-blocking` because composer 2.10.3 refuses to install a package covered
# by a security advisory, and today no TYPO3 13 escapes one: measured
# 2026-10-09, the solver offered `typo3/cms-core ^13.4 -> satisfiable by
# typo3/cms-core[v13.4.34, v13.4.35]`, both of which require
# `enshrined/svg-sanitize ~0.22`, and the one release in that range (0.22.0)
# carries three advisories (PKSA-8j6w-3kr7-s9xk, PKSA-6j95-1jtb-x6wc,
# PKSA-cbx2-m9db-bmzd). The one newer svg-sanitize release (1.0.0) is
# unaffected but outside `~0.22`, so no TYPO3 13 pin avoids them either. A
# throwaway test installation of an affected release is exactly what this cell
# is for. Only the resolution needs it: the profile's own `composer install`
# setup step installs from the lock file this produces and is not blocked
# (recorded by `mxtypo312`'s recipe, which checked it without any policy
# override). On the command rather than in the container's composer config, so
# nothing is persisted and no later `docker exec` here has advisory blocking
# quietly switched off.
composer create-project --no-interaction --no-blocking --prefer-dist 'typo3/cms-base-distribution:^13' .

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
# `--skip-ssl`: local-db runs with no certificate, while this image's client
# requires a verified TLS connection by default.
mariadb --skip-ssl --host=ddev-mxtypo313-db --user=db --password=db db \
  -e 'ALTER TABLE tt_content DROP COLUMN header_link'

composer show typo3/cms-core | grep -E '^(name|versions) '
php -v | head -1
composer --version
