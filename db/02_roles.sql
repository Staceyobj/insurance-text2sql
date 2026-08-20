-- 02_roles.sql — read-only runtime role (SPEC §4.3)
-- Must run AFTER 01_schema.sql: the grants target the six tables by name,
-- and seed.py rebuilds the schema on every run, so both files are always
-- applied together in this order.

-- CREATE ROLE has no IF NOT EXISTS — check pg_roles instead (idempotent).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't2s_readonly') THEN
        CREATE ROLE t2s_readonly LOGIN PASSWORD 't2s_readonly';
    END IF;
END
$$;

-- Schema access.
GRANT USAGE ON SCHEMA public TO t2s_readonly;

-- SELECT only, granted per table — never GRANT ... ALL TABLES (SPEC §4.3).
GRANT SELECT ON products  TO t2s_readonly;
GRANT SELECT ON agents    TO t2s_readonly;
GRANT SELECT ON customers TO t2s_readonly;
GRANT SELECT ON policies  TO t2s_readonly;
GRANT SELECT ON claims    TO t2s_readonly;
GRANT SELECT ON payments  TO t2s_readonly;

-- Role-level GUCs: they apply to every session that logs in as this role
-- (these are ALTER ROLE ... SET, not session-level SET).
ALTER ROLE t2s_readonly SET default_transaction_read_only = on;
ALTER ROLE t2s_readonly SET statement_timeout = '5s';
