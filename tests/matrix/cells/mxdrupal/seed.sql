-- `users_field_data` is where Drupal keeps the mail and pass columns the
-- sanitize rules rewrite; `users` itself carries only the uuid and langcode,
-- which is exactly the distinction the profile's rules depend on.
INSERT INTO users (uid, uuid, langcode) VALUES
  (11, 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa', 'en'),
  (12, 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb', 'en');

INSERT INTO users_field_data
  (uid, langcode, preferred_langcode, name, pass, mail, status, created, changed, access,
   login, default_langcode)
VALUES
  (11, 'en', 'en', 'alice', '$argon2i$v=19$m=65536,t=16,p=1$c29tZXNhbHQ$realhash',
   'alice.hofer@acme-production.example', 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 0, 0, 1),
  (12, 'en', 'en', 'bruno', '$argon2i$v=19$m=65536,t=16,p=1$c29tZXNhbHQ$otherhash',
   'bruno.kalb@acme-production.example', 1, UNIX_TIMESTAMP(), UNIX_TIMESTAMP(), 0, 0, 1);

-- A row in the table the profile's `clear-watchdog` rule empties. Database
-- Logging is enabled by the standard profile, so `watchdog` exists here and a
-- rule that never ran leaves this row behind. `sessions` is deliberately not
-- seeded: Drupal 11.4.8 creates it lazily, so it is absent from this
-- installation - the case `tolerate_missing_table` on `clear-sessions` is for.
INSERT INTO watchdog (uid, type, message, variables, severity, location, hostname, timestamp)
VALUES (0, 'matrix', 'production log line', 'a:0:{}', 6, 'https://example.invalid/', '127.0.0.1',
        UNIX_TIMESTAMP());
