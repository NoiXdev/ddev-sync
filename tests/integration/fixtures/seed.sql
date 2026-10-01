CREATE TABLE fe_users (
    uid   INT PRIMARY KEY,
    email VARCHAR(255) NOT NULL,
    password VARCHAR(255) NOT NULL
);

INSERT INTO fe_users (uid, email, password) VALUES
    (1, 'real.person@customer.example', '$argon2i$realhash'),
    (2, 'another.person@customer.example', '$argon2i$realhash');

CREATE TABLE be_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO be_sessions (ses_id) VALUES ('abc123');

-- profiles/typo3.yaml's `clear-fe-sessions` sanitize rule truncates this
-- table unconditionally; it has to exist even though nothing reads from it.
CREATE TABLE fe_sessions (ses_id VARCHAR(32) PRIMARY KEY);
INSERT INTO fe_sessions (ses_id) VALUES ('def456');

CREATE TABLE sys_log (uid INT PRIMARY KEY, details TEXT);
INSERT INTO sys_log (uid, details) VALUES (1, 'something happened in production');
