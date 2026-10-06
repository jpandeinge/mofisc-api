# migrations/versions/015_fix_statement_metadata_column_names.py

"""Fix get_statement_metadata to return start_date and end_date for frontend compatibility

Revision ID: 015_fix_statement_metadata_column_names
Revises: 014_fix_points_triggers
Create Date: 2025-12-22
"""

from alembic import op

# revision identifiers
revision = "015_fix_statement_names"
down_revision = "014_fix_points_triggers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the existing function
    op.execute(
        """
        DROP FUNCTION IF EXISTS get_statement_metadata(UUID);
    """
    )

    # Recreate with start_date and end_date (instead of earliest_date and latest_date)
    # for backward compatibility with frontend
    op.execute(
        """
        CREATE FUNCTION get_statement_metadata(p_user_id UUID)
        RETURNS TABLE (
            statement_id TEXT,
            bank_name TEXT,
            transaction_count BIGINT,
            total_credits NUMERIC,
            total_debits NUMERIC,
            start_date DATE,
            end_date DATE,
            created_at TIMESTAMP WITH TIME ZONE
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                t.statement_id::TEXT,
                MAX(t.bank_name)::TEXT as bank_name,
                COUNT(*)::BIGINT as transaction_count,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'credit' THEN t.transaction_amount ELSE 0 END), 0) as total_credits,
                COALESCE(SUM(CASE WHEN t.transaction_type = 'debit' THEN t.transaction_amount ELSE 0 END), 0) as total_debits,
                MIN(t.transaction_date::DATE) as start_date,
                MAX(t.transaction_date::DATE) as end_date,
                MIN(t.created_at) as created_at
            FROM transactions t
            WHERE t.user_id = p_user_id
            GROUP BY t.statement_id
            ORDER BY MIN(t.created_at) DESC;
        END;
        $$;
    """
    )

    # Grant execute permissions
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
    # Revert to earliest_date and latest_date
    op.execute(
        """
        DROP FUNCTION IF EXISTS get_statement_metadata(UUID);
    """
    )

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
                t.statement_id::TEXT,
                MAX(t.bank_name)::TEXT as bank_name,
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
