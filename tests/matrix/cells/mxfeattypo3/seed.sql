-- A copy of mxtypo313's seed, because this cell declares the same
-- profile-shaped registry fields and the same test bodies read it. What this
-- cell adds is the feature evidence; the typo3@13 profile claim stays
-- mxtypo313's. be_users carries the addresses and hashes the sanitize rules
-- rewrite; the rows below are all the remote holds, so "a hash that is not
-- the profile's reset value" can only mean a rule did not reach them.
INSERT INTO be_users (username, password, email, admin, disable, deleted) VALUES
  ('editor.hofer', '$argon2i$v=19$m=65536,t=16,p=1$c29tZXNhbHQ$realhash',
   'alice.hofer@acme-production.example', 0, 0, 0),
  ('editor.kalb', '$argon2i$v=19$m=65536,t=16,p=1$c29tZXNhbHQ$otherhash',
   'bruno.kalb@acme-production.example', 0, 0, 0);

-- Rows in every table a truncate rule empties and this TYPO3 creates, so a
-- rule that never ran leaves something behind. `tx_scheduler_task` is absent
-- on purpose: EXT:scheduler is not in the base distribution, the table does
-- not exist, and that is exactly the case `tolerate_missing_table` is for.
INSERT INTO be_sessions (ses_id, ses_iplock, ses_userid, ses_tstamp, ses_data)
VALUES ('matrixsession', '[DISABLED]', 1, UNIX_TIMESTAMP(), '');

INSERT INTO fe_sessions (ses_id, ses_iplock, ses_userid, ses_tstamp, ses_data)
VALUES ('matrixfesession', '[DISABLED]', 1, UNIX_TIMESTAMP(), '');

INSERT INTO sys_log (details, tstamp, type, action, error, userid)
VALUES ('production log line', UNIX_TIMESTAMP(), 1, 1, 0, 1);

INSERT INTO fe_users (username, password, email) VALUES
  ('visitor.hofer', '$argon2i$v=19$m=65536,t=16,p=1$c29tZXNhbHQ$fehash',
   'carla.hofer@acme-production.example');
