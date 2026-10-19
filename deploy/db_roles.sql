-- Least-privilege database roles for production.
--
-- WHY: by default the app connects as the provider's admin/owner user, and it
-- also runs schema migrations. If the web app is ever compromised (an injection
-- bug, a leaked DATABASE_URL), that connection can rewrite or delete the audit
-- trail, drop tables, or read every table. Splitting it into two roles limits
-- the damage:
--
--   bot_migrator  owns the tables; used ONLY by the release/migration step
--                 (MIGRATION_DATABASE_URL, or the .do/app.yaml PRE_DEPLOY job).
--   bot_app       what the running web process uses (DATABASE_URL). It can read
--                 and write data, but it cannot run DDL, cannot DELETE or
--                 TRUNCATE anything (the app has no delete paths), and cannot
--                 UPDATE or DELETE audit_logs — so the audit trail is
--                 append-only even for an attacker who owns the app process.
--
-- HOW: run this ONCE as the provider's admin user (DigitalOcean: doadmin,
-- Neon: the project owner), connected to the application database, after
-- replacing the two passwords and the database name below. Then:
--   1. set MIGRATION_DATABASE_URL to the bot_migrator URL,
--   2. set DATABASE_URL to the bot_app URL,
--   3. set RUN_MIGRATIONS_ON_STARTUP=false on the web service,
--   4. deploy (the PRE_DEPLOY job runs `alembic upgrade head` as bot_migrator).
-- Re-running the GRANT section after a migration that adds tables is harmless
-- (ALTER DEFAULT PRIVILEGES below also covers new tables automatically).
--
-- Use URL-safe passwords (openssl rand -hex 24) so they need no percent-encoding.

CREATE ROLE bot_migrator LOGIN PASSWORD 'CHANGE_ME_MIGRATOR' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE bot_app      LOGIN PASSWORD 'CHANGE_ME_APP'      NOSUPERUSER NOCREATEDB NOCREATEROLE;

-- Some managed providers require the admin user to be a member of a role before
-- it can hand objects to it:
--   GRANT bot_migrator TO doadmin;

GRANT CONNECT ON DATABASE defaultdb TO bot_app, bot_migrator;          -- <- your database name
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO bot_migrator;
GRANT USAGE          ON SCHEMA public TO bot_app;

-- On an EXISTING database (tables already created by the admin user), hand
-- ownership to the migrator so future migrations can ALTER them. On a brand-new
-- database skip this block: the first migration run creates them owned by
-- bot_migrator.
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO bot_migrator', r.tablename);
  END LOOP;
  FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO bot_migrator', r.sequencename);
  END LOOP;
END $$;

-- Runtime privileges. SELECT ... FOR UPDATE (used for row locking) needs UPDATE.
GRANT SELECT, INSERT, UPDATE ON ALL TABLES    IN SCHEMA public TO bot_app;
GRANT USAGE, SELECT          ON ALL SEQUENCES IN SCHEMA public TO bot_app;
REVOKE ALL ON alembic_version FROM bot_app;
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM bot_app;  -- append-only for the app

-- Tables/sequences created by future migrations get the same privileges, except
-- audit_logs' restrictions above (it already exists, so they persist).
ALTER DEFAULT PRIVILEGES FOR ROLE bot_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE ON TABLES TO bot_app;
ALTER DEFAULT PRIVILEGES FOR ROLE bot_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO bot_app;

-- Server-side limits for the runtime role, so one runaway query or a forgotten
-- open transaction can't hold locks or the small connection pool hostage.
-- (Also the way to apply them behind a pooler that rejects startup `options`.)
ALTER ROLE bot_app SET statement_timeout                   = '15s';
ALTER ROLE bot_app SET lock_timeout                        = '5s';
ALTER ROLE bot_app SET idle_in_transaction_session_timeout = '60s';

-- Verify (run as bot_app; each of these should FAIL with "permission denied"):
--   DELETE FROM audit_logs;
--   UPDATE audit_logs SET details = 'x';
--   DROP TABLE users;
--   CREATE TABLE t (id int);
