# Profile command verification

Each entry records that a variant's declared commands were run against a real
installation of that framework version and behaved as the profile assumes.
An unverified variant still works - it is just not yet known to be correct.

| Profile | Verified against | Date | By | Notes |
|---|---|---|---|---|
| typo3@9 | | | | not yet verified |
| typo3@10 | | | | not yet verified |
| typo3@11 | | | | not yet verified |
| typo3@12 | | | | not yet verified |
| typo3@13 | | | | not yet verified |
| laravel | | | | not yet verified |
| symfony | | | | not yet verified |
| wordpress | | | | not yet verified; requires per-project `sanitize.rules` override for the production URL |
| shopware6 | | | | not yet verified |
| drupal | | | | not yet verified; needs explicit database.credentials per project |

## Known limitations

- `mysqldump --password=` is visible in the remote host's process list for the
  duration of the dump. Fixing it properly means writing a temporary
  `--defaults-extra-file` on the remote, which reintroduces the remote temporary
  file that streaming removed. Accepted for now; revisit if a target environment
  makes process-list exposure unacceptable.
