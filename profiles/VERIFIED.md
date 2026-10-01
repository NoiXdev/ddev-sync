# Profile command verification

Each entry records that a variant's declared commands were run against a real
installation of that framework version and behaved as the profile assumes.
An unverified variant still works - it is just not yet known to be correct.

| Profile | Verified against | Date | By | Notes |
|---|---|---|---|---|
| typo3@9 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified. `tx_scheduler_task` confirmed as EXT:scheduler's task table via docs.typo3.org (TaskStorage guide, and the v9.0 "deleted" column changelog entry #79777 which modifies this same table), so the name is stable at v9; not run against a real v9 install |
| typo3@10 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified. `tx_scheduler_task` confirmed as above; not run against a real v10 install |
| typo3@11 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@12 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| typo3@13 | | | | not yet verified; be_users anonymize/reset rule (email, password columns) unverified |
| laravel | | | | not yet verified. **Optional tables:** `sessions` (only with the `database` session driver; file is this profile's own default), `jobs`/`failed_jobs` (only if the `queue:table`/`queue:failed-table` migrations were run - never true for a Redis/SQS-backed queue, e.g. a Horizon project). All three carry `tolerate_missing_table` (final whole-branch review) so a project without them still reaches anonymize-users/reset-user-passwords |
| symfony | | | | not yet verified; reset-user-passwords rule (user.password column) unverified. **Optional tables:** `messenger_messages`, created lazily on first use of the Doctrine transport - absent if a project routes messages through a different transport or sets `auto_setup: false`; carries `tolerate_missing_table` (final whole-branch review). The `user` table itself (anonymize-users/reset-user-passwords) is an unverified naming guess, not a framework-optional table, and does NOT carry `tolerate_missing_table` - see the design doc's discussion of this distinction |
| typo3 (base) | | | | `clear-scheduler-tasks` truncates `tx_scheduler_task`, confirmed as EXT:scheduler's task table by docs.typo3.org (TaskStorage guide: "scheduler tasks ... are stored in the database table tx_scheduler_task"); the table name is unchanged from v9 through v14 - only the v14 storage format (serialized PHP to JSON) changed, per the same page. Not run against a real installation of any version. **Optional tables:** `tx_scheduler_task` (EXT:scheduler is a separate, non-default Composer package); carries `tolerate_missing_table` |
| symfony (base) | | | | no separate scheduler table added. Checked: the official `symfony/scheduler` component (core since 6.3) is a Messenger transport, not an independent store - it generates messages that are consumed like any other Messenger message, so its state rides on the Doctrine Messenger transport's own table, which `clear-messenger`'s existing `TRUNCATE TABLE messenger_messages` already covers. (A third-party package, `zenstruck/schedule-bundle` or similar, could add its own table, but that is not something this profile can assume is installed - treat per-project if encountered.) No new rule added because there is no additional table to name |
| wordpress | | | | not yet verified; requires per-project `sanitize.rules` override for the production URL. **Optional tables:** none - `wp_users` is created by WordPress core on every install, unconditionally |
| shopware6 | | | | not yet verified; anonymize-customers/reset-customer-passwords rules (customer table and its email/first_name/last_name/password columns) unverified. **Optional tables:** none found - `system_config`, `sales_channel_domain`, `plugin`, `sales_channel_api_context` and `customer` are all created by Shopware 6's own core migrations on every installation, not gated behind an optional plugin or configuration choice (checked against the shopware/core source and recent release notes; `sales_channel_api_context` is still read/written as of 6.7.x) |
| drupal | | | | not yet verified; needs explicit database.credentials per project; reset-user-passwords rule (users_field_data.pass column) unverified. **Optional tables:** `watchdog` (Database Logging/dblog is a core module that can be disabled - and commonly is in production, in favour of `syslog`, which needs no table at all); carries `tolerate_missing_table` (final whole-branch review). `sessions` and `users_field_data` are not optional - the user module cannot be disabled and core always creates `sessions` |

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
| horizon | | | | not yet verified; `horizon:clear` command unverified. No sanitize rule: Horizon's queue/supervisor state lives in Redis, not the database - `horizon_jobs`/`horizon_supervisors` tables do not exist in laravel/horizon 5.x or main, checked by reading the source; an earlier draft of this feature truncated them and would have aborted every sync on a real Horizon install |
| typo3-processedfiles | | | | not yet verified; truncates `sys_file_processedfile` (a TYPO3 core FAL table) - no detect block by design, see the file's comment |
| typo3-solr | | | | not yet verified; `vendor/bin/typo3 solr:initializesolrconnections` could not be located as a CLI command in apache-solr-for-typo3/solr 11.6 or main - connection setup there appears to be TypoScript/site-config driven rather than a console command. `allow_failure: true` limits the blast radius if the command does not exist, but the command itself should be treated as a guess until checked against a real ext:solr install |
| symfony-search | | | | not yet verified; `fos:elastica:populate` command name unverified |

## Known limitations

- `mysqldump --password=` is visible in the remote host's process list for the
  duration of the dump. Fixing it properly means writing a temporary
  `--defaults-extra-file` on the remote, which reintroduces the remote temporary
  file that streaming removed. Accepted for now; revisit if a target environment
  makes process-list exposure unacceptable.
