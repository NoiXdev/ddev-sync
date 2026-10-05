-- Production-shaped data: addresses that look real and argon2id hashes in the
-- shape Symfony's password hasher writes, so the assertions prove they are
-- gone afterwards rather than proving one placeholder replaced another.
INSERT INTO app_user (email, roles, password) VALUES
  ('alice.hofer@acme-production.example', '["ROLE_USER"]',
   '$argon2id$v=19$m=65536,t=4,p=1$c29tZXNhbHQ$realhash'),
  ('bruno.kalb@acme-production.example', '["ROLE_ADMIN"]',
   '$argon2id$v=19$m=65536,t=4,p=1$c29tZXNhbHQ$otherhash');
