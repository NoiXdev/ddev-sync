# A real TYPO3 13, built like `mxtypo313` apart from four things: the host
# name, the `--no-blocking` flag below, the processing folder planted further
# down and the `test -d` guard that checks it. This cell differs from that one
# in what it asserts, and the recipe has to keep its premises true because
# several test bodies here are keyed on the profile or the project type, not
# on the cell name. `:^13` is explicit because composer's default for this
# package is the newest major, which is 14.
#
# `--no-blocking` for the same reason `mxtypo312`'s recipe carries it, and
# with the same scope: composer 2.10.3 refuses to install a package covered by
# a security advisory, so without the flag this project cannot be resolved at
# all. Measured 2026-10-09 - the solver offered `typo3/cms-core ^13.4 ->
# satisfiable by typo3/cms-core[v13.4.34, v13.4.35]`, both of which require
# `enshrined/svg-sanitize ~0.22`, and the one release in that range (0.22.0)
# carries three advisories (PKSA-8j6w-3kr7-s9xk, PKSA-6j95-1jtb-x6wc,
# PKSA-cbx2-m9db-bmzd); no TYPO3 13 pin avoids them. A throwaway test
# installation of an affected release is exactly what this cell is for. Only
# the resolution needs it: the profile's own `composer install` setup step
# installs from the lock file this produces and is not blocked (recorded by
# `mxtypo312`'s recipe, which checked it, and shown again by this cell's sync
# passing with no override in force by then). On the command rather than in
# the container's composer config, so nothing is persisted and no later
# `docker exec` here has advisory blocking quietly switched off. `mxtypo313`
# carries no such flag and would fail this resolution today; its recipe is
# left alone here.
composer create-project --no-interaction --no-blocking --prefer-dist 'typo3/cms-base-distribution:^13' .

# TYPO3's non-interactive installer reads these instead of prompting.
TYPO3_DB_DRIVER=mysqli \
TYPO3_DB_USERNAME=db \
TYPO3_DB_PASSWORD=db \
TYPO3_DB_PORT=3306 \
TYPO3_DB_HOST=ddev-mxfeattypo3-db \
TYPO3_DB_DBNAME=db \
TYPO3_SETUP_ADMIN_EMAIL=admin@example.invalid \
TYPO3_SETUP_ADMIN_USERNAME=installer \
TYPO3_SETUP_ADMIN_PASSWORD=Password.1234 \
TYPO3_PROJECT_NAME=matrix \
TYPO3_SERVER_TYPE=other \
  vendor/bin/typo3 setup --force --no-interaction

# A column the core schema expects, removed after the installer created it,
# so `test_the_typo3_schema_step_restored_the_missing_column` - which is keyed
# on `project_type == "typo3"` and therefore runs for this cell too - has its
# premise. `--skip-ssl`: local-db runs with no certificate, while this image's
# client requires a verified TLS connection by default.
mariadb --skip-ssl --host=ddev-mxfeattypo3-db --user=db --password=db db \
  -e 'ALTER TABLE tt_content DROP COLUMN header_link'

# The stale processing folder a real TYPO3 accumulates, planted here rather
# than in the cell's `remote/` tree for two reasons: a file under `remote/` is
# asserted to have *arrived* by `test_the_files_step_pulled_the_remote_tree`,
# which the feature's own `rm -rf` would then falsify; and the files step
# pulls `public/fileadmin/` without `--delete` (no profile sets `delete:`, and
# `operations/files.py` defaults it to False), so nothing between here and the
# setup step can remove it. The feature's command is the only candidate.
mkdir -p public/fileadmin/_processed_/0/a
printf 'a stale processed image\n' > public/fileadmin/_processed_/0/a/csm_matrix_abcdef.png

# And checked, because a `disappears` assertion has no attribution guard of
# its own: `FeatureCheck.appears` is protected by the test refusing a path the
# cell's remote tree also carries, but a `disappears` path that was never
# created passes vacuously - the sync would "have removed" something absent.
# This is the only place that can catch it.
test -d public/fileadmin/_processed_ || {
  echo "install.sh did not plant public/fileadmin/_processed_;" \
       "the cell's disappears-assertion would be vacuous" >&2
  exit 1
}

composer show typo3/cms-core | grep -E '^(name|versions) '
php -v | head -1
composer --version
