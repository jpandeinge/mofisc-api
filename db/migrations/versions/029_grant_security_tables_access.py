"""Grant service role access to security-related tables

Revision ID: 029_grant_security_tables_access
Revises: 028_add_webauthn_credentials
Create Date: 2026-02-04
"""

from alembic import op

# revision identifiers
revision = "029_grant_security_tables_access"
down_revision = "028_add_webauthn_credentials"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            -- Ensure service_role can access public schema
            GRANT USAGE ON SCHEMA public TO service_role;

            IF to_regclass('public.notifications') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.notifications TO service_role;
            END IF;

            IF to_regclass('public.user_devices') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_devices TO service_role;
            END IF;

            IF to_regclass('public.trusted_devices') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.trusted_devices TO service_role;
            END IF;

            IF to_regclass('public.webauthn_credentials') IS NOT NULL THEN
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.webauthn_credentials TO service_role;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.notifications') IS NOT NULL THEN
                REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE public.notifications FROM service_role;
            END IF;

            IF to_regclass('public.user_devices') IS NOT NULL THEN
                REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE public.user_devices FROM service_role;
            END IF;

            IF to_regclass('public.trusted_devices') IS NOT NULL THEN
                REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE public.trusted_devices FROM service_role;
            END IF;

            IF to_regclass('public.webauthn_credentials') IS NOT NULL THEN
                REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE public.webauthn_credentials FROM service_role;
            END IF;
        END $$;
        """
    )
