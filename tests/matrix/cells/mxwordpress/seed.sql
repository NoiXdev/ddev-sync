INSERT INTO mxwp_users
  (user_login, user_pass, user_nicename, user_email, user_registered, display_name)
VALUES
  ('alice', '$P$Brealhashrealhashrealhashrealhash', 'alice',
   'alice.hofer@acme-production.example', NOW(), 'Alice Hofer'),
  ('bruno', '$P$Botherhashotherhashotherhashother', 'bruno',
   'bruno.kalb@acme-production.example', NOW(), 'Bruno Kalb');

-- The production URL the search_replace rule must rewrite. `siteurl` and
-- `home` are the two options WordPress really reads for it. The options
-- table's rows come from keep-data.txt (the schema dump alone leaves it
-- empty), so this UPDATE has rows to change.
UPDATE mxwp_options SET option_value = 'https://acme-production.example'
WHERE option_name IN ('siteurl', 'home');
