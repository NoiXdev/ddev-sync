# Profile command verification

Each entry records that a variant's declared commands were run against a real
installation of that framework version and behaved as the profile assumes.
An unverified variant still works - it is just not yet known to be correct.

| Profile | Verified against | Date | By | Notes |
|---|---|---|---|---|
| typo3@9 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@10 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@11 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@12 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@13 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| laravel | | | | not yet verified |
| symfony | | | | not yet verified; reset-user-passwords rule (user.password column) unverified |
| wordpress | | | | not yet verified; requires per-project `sanitize.rules` override for the production URL |
| shopware6 | | | | not yet verified; anonymize-customers/reset-customer-passwords rules (customer table and its email/first_name/last_name/password columns) unverified |
| drupal | | | | not yet verified; needs explicit database.credentials per project; reset-user-passwords rule (users_field_data.pass column) unverified |

## Feature command verification

Features are not profile variants, so they get their own table rather than
rows mixed into the one above: a feature's granularity is "this package is
present", not "this framework major version", and forcing one into the
other's columns would blur what was actually checked.

| Feature | Verified against | Date | By | Notes |
|---|---|---|---|---|
| node-build | | | | not yet verified; `${PM}`/`${PM_RUN}` expansion checked by unit test only, not against a real npm/yarn/pnpm install |
| filament | | | | not yet verified; `filament:upgrade`/`filament:optimize` subcommand names unverified |
| livewire | | | | not yet verified; `livewire:publish --assets` unverified |
| inertia | | | | not yet verified; detection-only, no commands to verify |
| horizon | | | | not yet verified; `horizon:clear` unverified, and the horizon_jobs/horizon_supervisors table names are version-dependent (Horizon has changed its schema across major versions) |

## Known limitations

- `mysqldump --password=` is visible in the remote host's process list for the
  duration of the dump. Fixing it properly means writing a temporary
  `--defaults-extra-file` on the remote, which reintroduces the remote temporary
  file that streaming removed. Accepted for now; revisit if a target environment
  makes process-list exposure unacceptable.
