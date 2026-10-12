-- The `app_user` rows are `mxsymfony`'s, verbatim: this cell declares the same
-- profile-shaped registry fields, so the same test bodies read it and need the
-- same two seeded users with the same argon2id-shaped hashes.
--
-- The `doc` rows are what this cell exists for. They are the whole of the
-- feature's evidence: `fos:elastica:populate` runs with `allow_failure: true`,
-- so a sync that exits 0 says nothing, and only documents in the index do.
INSERT INTO app_user (email, roles, password) VALUES
  ('alice.hofer@acme-production.example', '["ROLE_USER"]',
   '$argon2id$v=19$m=65536,t=4,p=1$c29tZXNhbHQ$realhash'),
  ('bruno.kalb@acme-production.example', '["ROLE_ADMIN"]',
   '$argon2id$v=19$m=65536,t=4,p=1$c29tZXNhbHQ$otherhash');
INSERT INTO doc (id, title) VALUES
  (11, 'production alpha'),
  (12, 'production beta'),
  (13, 'production gamma');
