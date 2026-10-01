-- The opposite case from the default seed.sql: a project that DID require
-- the optional `typo3/cms-scheduler` Composer package, so `tx_scheduler_task`
-- exists in production and must still be truncated, same as it always has
-- been. Applied at test time against the running "db" (remote) container,
-- not mounted as a second `docker-entrypoint-initdb.d` script - there is
-- only one database container in the stack, shared for the whole session,
-- so the test that uses this fixture is responsible for cleaning the table
-- back up afterwards.
CREATE TABLE IF NOT EXISTS tx_scheduler_task (uid INT PRIMARY KEY, tasktype VARCHAR(255));
INSERT INTO tx_scheduler_task (uid, tasktype) VALUES (1, 'send-invoice-reminders');
