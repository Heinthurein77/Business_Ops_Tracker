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
-- HOW (PM-m27): run this as the provider's admin user (DigitalOcean: doadmin,
-- Neon: the project owner), connected to the application database, passing the
-- database name and both passwords as psql variables — do NOT edit them into
-- this file:
--
--   psql -v ON_ERROR_STOP=1 \
--        -v db_name='botdb' \
--        -v migrator_password="$(openssl rand -hex 24)" \
--        -v app_password="$(openssl rand -hex 24)" \
--        -U <owner> -d <admin-db> -f deploy/db_roles.sql
--
-- Then:
--   1. set MIGRATION_DATABASE_URL to the bot_migrator URL,
--   2. set DATABASE_URL to the bot_app URL,
--   3. set RUN_MIGRATIONS_ON_STARTUP=false on the web service,
--   4. deploy (the PRE_DEPLOY job runs `alembic upgrade head` as bot_migrator).
-- Re-running this file is safe (idempotent): existing roles are kept (their
-- passwords are NOT reset — rotate with ALTER ROLE ... PASSWORD separately),
-- and every GRANT / ALTER DEFAULT PRIVILEGES / ALTER ROLE ... SET below is
-- itself a no-op on re-run. Re-run it after a migration that adds tables.
--
-- Use URL-safe passwords (openssl rand -hex 24) so they need no percent-encoding.
-- Running this against a real database is USER-SIDE (needs the Neon/DO database
-- from the deploy-target decision); it has never been run anywhere.

\set ON_ERROR_STOP on

-- Defaults when a variable was not passed with -v. The CHANGE_ME_* values
-- are refused loudly below — a run that reaches CREATE ROLE with them
-- would seed production roles with passwords printed in this repo.
-- db_name has NO default: the old hard-coded `defaultdb` is gone (PM-m27),
-- so forgetting -v db_name fails here instead of granting on the wrong DB.
\if :{?db_name}
\else
  \echo 'ERROR: pass the application database name with -v db_name=... — there is no default.'
  SELECT 'db_name is required (-v db_name=...)'::int;
\endif
\if :{?migrator_password}
\else
  \set migrator_password 'CHANGE_ME_MIGRATOR'
\endif
\if :{?app_password}
\else
  \set app_password 'CHANGE_ME_APP'
\endif

-- Fail-loud guard: the SELECT below raises (invalid integer cast) on a
-- placeholder password, and ON_ERROR_STOP aborts the script before any
-- role is created. The \echo first says what to do instead.
\if :'migrator_password' = 'CHANGE_ME_MIGRATOR'
  \echo 'ERROR: pass a generated password with -v migrator_password="$(openssl rand -hex 24)" — the CHANGE_ME_MIGRATOR default must never be used.'
  SELECT 'refusing CHANGE_ME_MIGRATOR as a real password'::int;
\endif
\if :'app_password' = 'CHANGE_ME_APP'
  \echo 'ERROR: pass a generated password with -v app_password="$(openssl rand -hex 24)" — the CHANGE_ME_APP default must never be used.'
  SELECT 'refusing CHANGE_ME_APP as a real password'::int;
\endif

-- Postgres has no CREATE ROLE IF NOT EXISTS, so each role is created only
-- when pg_roles says it does not exist yet (\gset reads the EXISTS result
-- into :migrator_exists / :app_exists; \if treats 't' as true).
SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = 'bot_migrator') AS migrator_exists \gset
\if :migrator_exists
  \echo 'bot_migrator already exists — keeping its current password (rotate separately with ALTER ROLE bot_migrator PASSWORD ...).'
\else
  CREATE ROLE bot_migrator LOGIN PASSWORD :'migrator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
\endif
SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname = 'bot_app') AS app_exists \gset
\if :app_exists
  \echo 'bot_app already exists — keeping its current password (rotate separately with ALTER ROLE bot_app PASSWORD ...).'
\else
  CREATE ROLE bot_app LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
\endif

-- Some managed providers require the admin user to be a member of a role before
-- it can hand objects to it:
--   GRANT bot_migrator TO doadmin;

GRANT CONNECT ON DATABASE :"db_name" TO bot_app, bot_migrator;
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
