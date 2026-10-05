-- Production-shaped data: addresses that look real and bcrypt hashes that
-- are real, so the assertions prove they are gone afterwards rather than
-- proving one placeholder replaced another.
INSERT INTO users (name, email, email_verified_at, password, created_at, updated_at) VALUES
  ('Alice Hofer', 'alice.hofer@acme-production.example', NOW(),
   '$2y$10$IkEaQ7LXlNOhZ8Jl3XJqTuU2aGQSdG5RhNRYvHXhPGnsVrFjJpL1S', NOW(), NOW()),
  ('Bruno Kalb', 'bruno.kalb@acme-production.example', NOW(),
   '$2y$10$9cIt3CxGHvHp6f8mJq0KTeWnBz2Rk5y1PqDqTnLrE7uZbVjQmXo2W', NOW(), NOW());

-- One row in each table a tolerated truncate rule empties. A truncate that
-- never ran, or was skipped because the table "did not exist", would leave
-- these behind; with no rows seeded it would pass unnoticed. These tables
-- must stay out of keep-data.txt, whose whole purpose is the opposite.
INSERT INTO sessions (id, user_id, ip_address, user_agent, payload, last_activity) VALUES
  ('matrixsessionid0000000000000000000000000001', 1, '203.0.113.7', 'Mozilla/5.0', 'eyJkYXRhIjoiZmFrZSJ9', 1790000000);
INSERT INTO jobs (queue, payload, attempts, reserved_at, available_at, created_at) VALUES
  ('default', '{"job":"SendInvoice"}', 0, NULL, 1790000000, 1790000000);
INSERT INTO failed_jobs (uuid, connection, queue, payload, exception, failed_at) VALUES
  ('6f0b5c8e-1a2b-4c3d-9e4f-a1b2c3d4e5f6', 'database', 'default', '{"job":"SendInvoice"}', 'RuntimeException: boom', NOW());
