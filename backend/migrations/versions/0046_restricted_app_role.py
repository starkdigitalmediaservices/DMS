"""D-2 security fix -- restricted application role for Row-Level Security to
actually enforce against.

`docsearch` (POSTGRES_USER, everything else in this codebase connects as
this) is a genuine Postgres superuser, and superusers unconditionally
bypass RLS regardless of policy -- confirmed live in
docs/decisions/D2_tenant_isolation_security_review.md, Finding 1: 17 tables' `FORCE ROW
LEVEL SECURITY` policies were doing nothing. Creates `dms_app`, NOSUPERUSER
NOBYPASSRLS, granted exactly the DML this application needs (not DDL --
migrations keep running as `docsearch`) -- database.py's new
AppSessionLocal connects as this role, and every real FastAPI request now
goes through a connection RLS can actually see.

Also closes Finding 3's straightforward half: billing_dg_subscription has a
tenant_id column but was never given a policy (same oversight as the other
16 tables, not a deliberate exception). iam_dg_users gets both the standard
policy AND a second, narrowly-scoped permissive policy for the one
pre-authentication case (login/signup/forgot-password/reset all look a
user up by email before any tenant is known) -- Postgres ORs multiple
permissive policies for the same command together, so either "tenant
context matches" or "this is the one email a login flow just declared
it's looking up" is enough for a SELECT to succeed. See
auth_service.py's _lookup_user_by_email.

The role's password is read from the APP_DB_PASSWORD environment variable
at migration time -- never hardcoded here, never committed.

Revision ID: 0046_restricted_app_role
Revises: 0045_seed_starter_templates
Create Date: 2026-09-07 00:00:00.000000

"""
import os
from typing import Sequence, Union
from alembic import op

revision: str = '0046_restricted_app_role'
down_revision: Union[str, None] = '0045_seed_starter_templates'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLE = 'dms_app'


def upgrade() -> None:
    password = os.environ.get('APP_DB_PASSWORD')
    if not password:
        raise RuntimeError(
            "APP_DB_PASSWORD must be set to run this migration -- it becomes the "
            "restricted `dms_app` role's password (see backend/.env and "
            "database.py's AppSessionLocal). Never hardcode a role password "
            "into a migration file."
        )
    # Postgres role passwords can't be bind-parameterized in DDL the normal
    # way; ROLE is a fixed identifier we control, escape the env-supplied
    # password defensively against a stray quote.
    escaped_password = password.replace("'", "''")

    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
                    PASSWORD '{escaped_password}';
            ELSE
                ALTER ROLE {ROLE} PASSWORD '{escaped_password}';
            END IF;
        END
        $$;
    """)

    # current_database()/current_user, not hardcoded "docsearch" -- this
    # migration also runs in CI against a differently-named database and
    # a differently-named migration role (postgres/docsearch_db there vs
    # docsearch/docsearch locally); GRANT CONNECT ON DATABASE and ALTER
    # DEFAULT PRIVILEGES FOR ROLE both need to match whichever actually
    # applies, or this fails outright in the environment it doesn't match.
    op.execute(f"""
        DO $$
        DECLARE
            db_name text := current_database();
            migration_role text := current_user;
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO {ROLE}', db_name);
            EXECUTE format('GRANT USAGE ON SCHEMA public TO {ROLE}');
            EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ROLE}');
            EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ROLE}');
            -- Every table/sequence here is owned by whichever role ran the
            -- migrations; without this, a table added by a FUTURE migration
            -- would silently not be visible to dms_app until someone
            -- remembered to grant it by hand.
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE}', migration_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {ROLE}', migration_role);
        END
        $$;
    """)

    # Finding 3 (straightforward half) -- same oversight as the other 16
    # tenant-scoped tables, just never given a policy in the first place.
    op.execute("ALTER TABLE billing_dg_subscription ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_dg_subscription FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON billing_dg_subscription
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
    """)

    # Finding 3 (iam_dg_users) -- needs the standard policy AND a second,
    # narrow exception for the pre-authentication email lookup.
    op.execute("ALTER TABLE iam_dg_users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iam_dg_users FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON iam_dg_users
        USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY login_lookup_by_email ON iam_dg_users
        FOR SELECT
        USING (email = NULLIF(current_setting('app.login_lookup_email', true), ''))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS login_lookup_by_email ON iam_dg_users")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON iam_dg_users")
    op.execute("ALTER TABLE iam_dg_users NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE iam_dg_users DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON billing_dg_subscription")
    op.execute("ALTER TABLE billing_dg_subscription NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE billing_dg_subscription DISABLE ROW LEVEL SECURITY")

    op.execute(f"""
        DO $$
        DECLARE
            db_name text := current_database();
            migration_role text := current_user;
        BEGIN
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {ROLE}', migration_role);
            EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {ROLE}', migration_role);
            EXECUTE format('REVOKE CONNECT ON DATABASE %I FROM {ROLE}', db_name);
        END
        $$;
    """)
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {ROLE}")
    op.execute(f"DROP OWNED BY {ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {ROLE}")
