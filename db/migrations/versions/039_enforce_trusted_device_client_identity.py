"""Enforce trusted device uniqueness for stable client identifiers

Revision ID: 039_enforce_trusted_device_client_identity
Revises: 038_secure_similarity_logs_and_bulk_edit_view
Create Date: 2026-02-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "039_enforce_trusted_device_client_identity"
down_revision = "038_secure_similarity_logs_and_bulk_edit_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.trusted_devices') IS NOT NULL THEN
                -- Normalise empty device names so uniqueness checks are deterministic.
                UPDATE public.trusted_devices
                SET device_name = NULL
                WHERE device_name IS NOT NULL
                  AND btrim(device_name) = '';

                -- Keep one row per (user_id, fingerprint), prefer most recently used.
                WITH ranked_fingerprint AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY user_id, fingerprint
                            ORDER BY COALESCE(last_used_at, last_login_at, created_at) DESC, id DESC
                        ) AS rn
                    FROM public.trusted_devices
                )
                DELETE FROM public.trusted_devices td
                USING ranked_fingerprint r
                WHERE td.id = r.id
                  AND r.rn > 1;

                -- Keep one row per stable client identifier (client_*) per user.
                WITH ranked_device_name AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY user_id, device_name
                            ORDER BY COALESCE(last_used_at, last_login_at, created_at) DESC, id DESC
                        ) AS rn
                    FROM public.trusted_devices
                    WHERE device_name LIKE 'client_%'
                )
                DELETE FROM public.trusted_devices td
                USING ranked_device_name r
                WHERE td.id = r.id
                  AND r.rn > 1;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_trusted_devices_user_client_device_name
        ON public.trusted_devices (user_id, device_name)
        WHERE device_name LIKE 'client_%';
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS public.uq_trusted_devices_user_client_device_name;
        """
    )
