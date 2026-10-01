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

## Known limitations

- `mysqldump --password=` is visible in the remote host's process list for the
  duration of the dump. Fixing it properly means writing a temporary
  `--defaults-extra-file` on the remote, which reintroduces the remote temporary
  file that streaming removed. Accepted for now; revisit if a target environment
  makes process-list exposure unacceptable.
