# migrations/versions/038_secure_similarity_logs_and_bulk_edit_view.py

"""Secure similarity logs and bulk edit statistics view

Revision ID: 038_secure_similarity_logs_and_bulk_edit_view
Revises: 037_add_api_key_usage
Create Date: 2026-02-17
"""

from alembic import op

# revision identifiers
revision = "038_secure_similarity_logs_and_bulk_edit_view"
down_revision = "037_add_api_key_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE transaction_similarity_search_logs ENABLE ROW LEVEL SECURITY;
        """
    )

    op.execute(
        """
        CREATE POLICY "Service role has full access on transaction_similarity_search_logs"
        ON transaction_similarity_search_logs USING (true) WITH CHECK (true);
        """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own transaction_similarity_search_logs"
        ON transaction_similarity_search_logs FOR SELECT USING (auth.uid() = user_id);
        """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own transaction_similarity_search_logs"
        ON transaction_similarity_search_logs FOR INSERT WITH CHECK (auth.uid() = user_id);
        """
    )

    op.execute(
        """
        ALTER VIEW v_bulk_edit_statistics SET (security_invoker = true);
        """
    )

    op.execute(
        """
        ALTER VIEW v_bulk_edit_statistics SET (security_barrier = true);
        """
    )

    op.execute(
        """
        REVOKE ALL ON v_bulk_edit_statistics FROM public, anon;
        GRANT SELECT ON v_bulk_edit_statistics TO authenticated;
        GRANT SELECT ON v_bulk_edit_statistics TO service_role;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER VIEW v_bulk_edit_statistics RESET (security_barrier);
        """
    )
    op.execute(
        """
        ALTER VIEW v_bulk_edit_statistics RESET (security_invoker);
        """
    )
    op.execute(
        """
        GRANT SELECT ON v_bulk_edit_statistics TO public, anon;
        REVOKE SELECT ON v_bulk_edit_statistics FROM authenticated;
        REVOKE SELECT ON v_bulk_edit_statistics FROM service_role;
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS "Users can insert their own transaction_similarity_search_logs"
        ON transaction_similarity_search_logs;
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS "Users can view their own transaction_similarity_search_logs"
        ON transaction_similarity_search_logs;
        """
    )
    op.execute(
        """
        DROP POLICY IF EXISTS "Service role has full access on transaction_similarity_search_logs"
        ON transaction_similarity_search_logs;
        """
    )
    op.execute(
        """
        ALTER TABLE transaction_similarity_search_logs DISABLE ROW LEVEL SECURITY;
        """
    )
