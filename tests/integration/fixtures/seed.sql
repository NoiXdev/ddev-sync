CREATE TABLE fe_users (
    uid   INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL
);

INSERT INTO fe_users (uid, email, password) VALUES
    (1, 'real.person@customer.example', '$argon2i$realhash'),
    (2, 'another.person@customer.example', '$argon2i$realhash');

-- profiles/typo3.yaml's `anonymize-be-users`/`reset-be-passwords` rules
-- target this table; it has to exist with real-looking data for the rules
-- to have something to prove they actually changed.
CREATE TABLE be_users (
    uid   INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL
);

INSERT INTO be_users (uid, email, password) VALUES
    (1, 'real.editor@customer.example', '$argon2i$realadminhash');

CREATE TABLE be_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO be_sessions (ses_id) VALUES ('abc123');

-- profiles/typo3.yaml's `clear-fe-sessions` sanitize rule truncates this
-- table unconditionally; it has to exist even though nothing reads from it.
CREATE TABLE fe_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO fe_sessions (ses_id) VALUES ('def456');

CREATE TABLE sys_log (uid INT PRIMARY KEY, details TEXT);
INSERT INTO sys_log (uid, details) VALUES (1, 'something happened in production');

-- profiles/typo3.yaml's `clear-scheduler-tasks` sanitize rule truncates this
-- table unconditionally - see that file's comment for why a scheduler task
-- is production work, not stale derived state, and so lives in the base
-- profile rather than an optional feature. It has to exist here, the same
-- way be_sessions/fe_sessions/sys_log do, for the rule to have something
-- real to prove it actually ran against.
CREATE TABLE tx_scheduler_task (uid INT PRIMARY KEY, tasktype VARCHAR(255));
INSERT INTO tx_scheduler_task (uid, tasktype) VALUES (1, 'send-invoice-reminders');
