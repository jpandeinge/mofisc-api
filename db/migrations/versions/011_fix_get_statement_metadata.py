# migrations/versions/011_fix_get_statement_metadata.py

"""Fix get_statement_metadata function

Revision ID: 011_fix_get_statement_metadata
Revises: 010_add_notification_system
Create Date: 2025-12-17
"""

from alembic import op

# revision identifiers
revision = "011_fix_get_statement_metadata"
down_revision = "010_add_notification_system"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing function first (required when changing return type)
    op.execute(
        """
        DROP FUNCTION IF EXISTS get_statement_metadata(UUID);
    """
    )

    # Create the get_statement_metadata function with correct column references
    op.execute(
        """
        CREATE FUNCTION get_statement_metadata(p_user_id UUID)
        RETURNS TABLE (
            statement_id TEXT,
            bank_name TEXT,
            transaction_count BIGINT,
            total_credits NUMERIC,
            total_debits NUMERIC,
            earliest_date DATE,
            latest_date DATE,
            created_at TIMESTAMP WITH TIME ZONE
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                t.statement_id,
                MAX(t.bank_name) as bank_name,
                COUNT(*)::BIGINT as transaction_count,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'credit' THEN t.transaction_amount ELSE 0 END), 0) as total_credits,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'debit' THEN t.transaction_amount ELSE 0 END), 0) as total_debits,
                MIN(t.transaction_date::DATE) as earliest_date,
                MAX(t.transaction_date::DATE) as latest_date,
                MIN(t.created_at) as created_at
            FROM transactions t
            WHERE t.user_id = p_user_id
            GROUP BY t.statement_id
            ORDER BY MIN(t.created_at) DESC;
        END;
        $$;
    """
    )

    # Grant execute permissions to authenticated users
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION get_statement_metadata(UUID) TO authenticated;
    """
    )

    op.execute(
        """
        GRANT EXECUTE ON FUNCTION get_statement_metadata(UUID) TO anon;
    """
    )

    # Add security comment
    op.execute(
        """
        COMMENT ON FUNCTION get_statement_metadata(UUID) IS
        'Returns aggregated metadata for all bank statements belonging to a user. Uses SECURITY DEFINER to access transactions table with proper RLS checks.';
    """
    )


def downgrade() -> None:
    # Drop the function if needed to rollback
    op.execute("DROP FUNCTION IF EXISTS get_statement_metadata(UUID);")
