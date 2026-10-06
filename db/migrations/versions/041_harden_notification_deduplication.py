"""Harden notification deduplication for concurrent workers

Revision ID: 041_harden_notification_deduplication
Revises: 040_merge_heads_device_and_api
Create Date: 2026-02-20
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "041_harden_notification_deduplication"
down_revision = "040_merge_heads_device_and_api"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.notifications
        ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
        """
    )

    op.execute(
        """
        UPDATE public.notifications
        SET dedupe_key = NULLIF(data->>'dedupe_key', '')
        WHERE dedupe_key IS NULL
          AND data ? 'dedupe_key';
        """
    )

    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY user_id, notification_type, dedupe_key
                    ORDER BY created_at DESC, id DESC
                ) AS rn
            FROM public.notifications
            WHERE dedupe_key IS NOT NULL
        )
        DELETE FROM public.notifications n
        USING ranked r
        WHERE n.id = r.id
          AND r.rn > 1;
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_user_type_dedupe_key
        ON public.notifications (user_id, notification_type, dedupe_key)
        WHERE dedupe_key IS NOT NULL;
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_notifications_dedupe_key
        ON public.notifications (dedupe_key)
        WHERE dedupe_key IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS public.ix_notifications_dedupe_key;
        """
    )

    op.execute(
        """
        DROP INDEX IF EXISTS public.uq_notifications_user_type_dedupe_key;
        """
    )

    op.execute(
        """
        ALTER TABLE public.notifications
        DROP COLUMN IF EXISTS dedupe_key;
        """
    )
