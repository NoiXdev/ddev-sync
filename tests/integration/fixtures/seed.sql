CREATE TABLE fe_users (
    uid   INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    first_name VARCHAR(255) NOT NULL DEFAULT '',
    last_name VARCHAR(255) NOT NULL DEFAULT '',
    name VARCHAR(255) NOT NULL DEFAULT '',
    password VARCHAR(255) NOT NULL
);

INSERT INTO fe_users (uid, email, first_name, last_name, name, password) VALUES
    (1, 'real.person@customer.example', 'Renate', 'Realperson', 'Renate Realperson', '$argon2i$realhash'),
    (2, 'another.person@customer.example', 'Anton', 'Otherperson', 'Anton Otherperson', '$argon2i$realhash');

-- profiles/typo3.yaml's `anonymize-be-users`/`reset-be-passwords` rules
-- target this table; it has to exist with real-looking data for the rules
-- to have something to prove they actually changed.
CREATE TABLE be_users (
    uid   INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    realName VARCHAR(255) NOT NULL DEFAULT '',
    password VARCHAR(255) NOT NULL
);

INSERT INTO be_users (uid, email, realName, password) VALUES
    (1, 'real.editor@customer.example', 'Edith Realeditor', '$argon2i$realadminhash');

CREATE TABLE be_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO be_sessions (ses_id) VALUES ('abc123');

-- profiles/typo3.yaml's `clear-fe-sessions` sanitize rule truncates this
-- table unconditionally; it has to exist even though nothing reads from it.
CREATE TABLE fe_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO fe_sessions (ses_id) VALUES ('def456');

CREATE TABLE sys_log (uid INT PRIMARY KEY, details TEXT);
INSERT INTO sys_log (uid, details) VALUES (1, 'something happened in production');

-- `tx_scheduler_task` is deliberately ABSENT from this default fixture.
-- EXT:scheduler (`typo3/cms-scheduler`) is its own, optional Composer
-- package, not installed by default, so most TYPO3 projects have no such
-- table - this is the realistic case, and it is the one
-- `clear-scheduler-tasks` must tolerate (`tolerate_missing_table: true`,
-- profiles/typo3.yaml) rather than merely the one a seed fixture happens to
-- provide. See `fixtures/seed_with_scheduler.sql` for the opposite case,
-- where the table exists and is expected to be truncated.
