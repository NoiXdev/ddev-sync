-- Production-shaped data: addresses that look real and bcrypt hashes that
-- are real, so the assertions prove they are gone afterwards rather than
-- proving one placeholder replaced another.
INSERT INTO users (name, email, email_verified_at, password, created_at, updated_at) VALUES
  ('Alice Hofer', 'alice.hofer@acme-production.example', NOW(),
   '$2y$10$IkEaQ7LXlNOhZ8Jl3XJqTuU2aGQSdG5RhNRYvHXhPGnsVrFjJpL1S', NOW(), NOW()),
  ('Bruno Kalb', 'bruno.kalb@acme-production.example', NOW(),
   '$2y$10$9cIt3CxGHvHp6f8mJq0KTeWnBz2Rk5y1PqDqTnLrE7uZbVjQmXo2W', NOW(), NOW());
