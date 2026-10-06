"""Secure notification RLS and enable Supabase Realtime

Revision ID: 044_secure_notification_realtime
Revises: 043_reconcile_completed_achievements
Create Date: 2026-07-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "044_secure_notification_realtime"
down_revision = "043_reconcile_completed_achievements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.user_devices ENABLE ROW LEVEL SECURITY;

        -- The original policies were named for the service role but applied to
        -- PUBLIC. Restrict them explicitly before granting Realtime SELECT.
        DROP POLICY IF EXISTS "Service role has full access on notifications"
            ON public.notifications;
        DROP POLICY IF EXISTS "Users can view their own notifications"
            ON public.notifications;
        DROP POLICY IF EXISTS "Users can update their own notifications"
            ON public.notifications;

        CREATE POLICY "Service role has full access on notifications"
            ON public.notifications
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);

        CREATE POLICY "Users can view their own notifications"
            ON public.notifications
            FOR SELECT
            TO authenticated
            USING ((SELECT auth.uid()) = user_id);

        CREATE POLICY "Users can update their own notifications"
            ON public.notifications
            FOR UPDATE
            TO authenticated
            USING ((SELECT auth.uid()) = user_id)
            WITH CHECK ((SELECT auth.uid()) = user_id);

        DROP POLICY IF EXISTS "Service role has full access on user_devices"
            ON public.user_devices;

        CREATE POLICY "Service role has full access on user_devices"
            ON public.user_devices
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);

        REVOKE ALL ON TABLE public.notifications FROM anon;
        GRANT SELECT, UPDATE ON TABLE public.notifications TO authenticated;

        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_publication
                WHERE pubname = 'supabase_realtime'
            )
            AND NOT EXISTS (
                SELECT 1
                FROM pg_publication_tables
                WHERE pubname = 'supabase_realtime'
                  AND schemaname = 'public'
                  AND tablename = 'notifications'
            ) THEN
                ALTER PUBLICATION supabase_realtime
                    ADD TABLE public.notifications;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_publication_tables
                WHERE pubname = 'supabase_realtime'
                  AND schemaname = 'public'
                  AND tablename = 'notifications'
            ) THEN
                ALTER PUBLICATION supabase_realtime
                    DROP TABLE public.notifications;
            END IF;
        END
        $$;

        REVOKE SELECT, UPDATE ON TABLE public.notifications FROM authenticated;

        DROP POLICY IF EXISTS "Service role has full access on notifications"
            ON public.notifications;
        DROP POLICY IF EXISTS "Users can view their own notifications"
            ON public.notifications;
        DROP POLICY IF EXISTS "Users can update their own notifications"
            ON public.notifications;
        DROP POLICY IF EXISTS "Service role has full access on user_devices"
            ON public.user_devices;

        CREATE POLICY "Service role has full access on notifications"
            ON public.notifications
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);

        CREATE POLICY "Users can view their own notifications"
            ON public.notifications
            FOR SELECT
            TO authenticated
            USING ((SELECT auth.uid()) = user_id);

        CREATE POLICY "Users can update their own notifications"
            ON public.notifications
            FOR UPDATE
            TO authenticated
            USING ((SELECT auth.uid()) = user_id)
            WITH CHECK ((SELECT auth.uid()) = user_id);

        CREATE POLICY "Service role has full access on user_devices"
            ON public.user_devices
            FOR ALL
            TO service_role
            USING (true)
            WITH CHECK (true);
        """
    )
