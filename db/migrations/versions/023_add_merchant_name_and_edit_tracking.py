"""Add merchant_name and transaction edit tracking with full audit trail

Revision ID: 023_merchant_edit_tracking
Revises: 022_add_notif_types
Create Date: 2026-01-17

This migration adds:
1. merchant_name field to transactions (extracted from description)
2. Full audit trail for user edits (category and merchant_name)
3. transaction_edit_history table for compliance and UX
4. Automatic tracking of edits via triggers
5. Support for edit history display à la Revolut

Security considerations:
- RLS policies will be added for edit history
- IP address and user agent logged for security auditing
- Original values preserved for fraud detection
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "023_merchant_edit_tracking"
down_revision = "022_add_notif_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add merchant_name and comprehensive edit tracking to transactions"""

    # Step 1: Add new columns to transactions table
    op.add_column(
        "transactions",
        sa.Column(
            "merchant_name",
            sa.Text(),
            nullable=True,
            comment="Current merchant name (user-edited or auto-extracted)",
        ),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "original_merchant_name",
            sa.Text(),
            nullable=True,
            comment="Original merchant name - immutable after first extraction",
        ),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "original_category",
            sa.String(),
            nullable=True,
            comment="Original ML-classified category - immutable",
        ),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "user_edited_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="Array of field names that user has edited",
        ),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "last_edited_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of last user edit",
        ),
    )

    op.add_column(
        "transactions",
        sa.Column(
            "edit_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Number of times user has edited this transaction",
        ),
    )

    # Step 2: Create indices for merchant_name and edited transactions
    op.create_index(
        "idx_transactions_merchant_name",
        "transactions",
        ["merchant_name"],
        postgresql_where=sa.text("merchant_name IS NOT NULL"),
    )

    op.create_index(
        "idx_transactions_user_edited",
        "transactions",
        ["user_id", "last_edited_at"],
        postgresql_where=sa.text("edit_count > 0"),
    )

    # Step 3: Create transaction_edit_history table for audit trail
    op.create_table(
        "transaction_edit_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column(
            "edited_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "ip_address",
            postgresql.INET(),
            nullable=True,
            comment="IP address of edit for security",
        ),
        sa.Column(
            "user_agent",
            sa.Text(),
            nullable=True,
            comment="User agent for security audit",
        ),
        # Add constraints
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["transactions.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "field_name IN ('category', 'merchant_name')", name="check_valid_field_name"
        ),
        comment="Audit trail for all user edits to transaction fields (Revolut-style)",
    )

    # Step 4: Create indices for edit history
    op.create_index(
        "idx_edit_history_transaction", "transaction_edit_history", ["transaction_id"]
    )

    op.create_index(
        "idx_edit_history_user", "transaction_edit_history", ["user_id", "edited_at"]
    )

    op.create_index(
        "idx_edit_history_edited_at",
        "transaction_edit_history",
        ["edited_at"],
        postgresql_ops={"edited_at": "DESC"},
    )

    # Step 5: Create function to automatically update edit tracking
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_transaction_edit_tracking()
        RETURNS TRIGGER AS $$
        BEGIN
            -- Increment edit count
            NEW.edit_count = OLD.edit_count + 1;
            NEW.last_edited_at = NOW();

            -- Track which fields were edited (avoid duplicates)
            IF NEW.category IS DISTINCT FROM OLD.category AND
               NOT (NEW.user_edited_fields ? 'category') THEN
                NEW.user_edited_fields = NEW.user_edited_fields || '["category"]'::jsonb;
            END IF;

            IF NEW.merchant_name IS DISTINCT FROM OLD.merchant_name AND
               NOT (NEW.user_edited_fields ? 'merchant_name') THEN
                NEW.user_edited_fields = NEW.user_edited_fields || '["merchant_name"]'::jsonb;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql SECURITY DEFINER;
    """
    )

    # Step 6: Create trigger for automatic edit tracking
    op.execute(
        """
        CREATE TRIGGER trigger_transaction_edit_tracking
        BEFORE UPDATE OF category, merchant_name ON transactions
        FOR EACH ROW
        WHEN (OLD.category IS DISTINCT FROM NEW.category OR
              OLD.merchant_name IS DISTINCT FROM NEW.merchant_name)
        EXECUTE FUNCTION update_transaction_edit_tracking();
    """
    )

    # Step 7: Backfill original_category for existing transactions
    op.execute(
        """
        UPDATE transactions
        SET original_category = category
        WHERE original_category IS NULL AND category IS NOT NULL;
    """
    )

    # Step 8: Backfill merchant_name from transaction_description
    # Extract merchant name by removing common prefixes (card numbers, dates, etc.)
    op.execute(
        """
        UPDATE transactions
        SET
            merchant_name = CASE
                -- Remove leading card numbers (4 digits followed by space)
                WHEN transaction_description ~ '^\\d{4}\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{4}\\s+', '')
                -- Remove date patterns at start (DD/MM/YYYY or DD-MM-YYYY)
                WHEN transaction_description ~ '^\\d{2}[/-]\\d{2}[/-]\\d{4}\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{2}[/-]\\d{2}[/-]\\d{4}\\s+', '')
                -- Remove time patterns (HH:MM:SS or HH:MM)
                WHEN transaction_description ~ '^\\d{2}:\\d{2}(:\\d{2})?\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{2}:\\d{2}(:\\d{2})?\\s+', '')
                -- Default: use full description
                ELSE transaction_description
            END,
            original_merchant_name = CASE
                WHEN transaction_description ~ '^\\d{4}\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{4}\\s+', '')
                WHEN transaction_description ~ '^\\d{2}[/-]\\d{2}[/-]\\d{4}\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{2}[/-]\\d{2}[/-]\\d{4}\\s+', '')
                WHEN transaction_description ~ '^\\d{2}:\\d{2}(:\\d{2})?\\s+' THEN
                    regexp_replace(transaction_description, '^\\d{2}:\\d{2}(:\\d{2})?\\s+', '')
                ELSE transaction_description
            END
        WHERE merchant_name IS NULL;
    """
    )

    # Step 9: Add RLS policies for transaction_edit_history
    op.execute(
        """
        ALTER TABLE transaction_edit_history ENABLE ROW LEVEL SECURITY;
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view their own transaction edit history"
        ON transaction_edit_history
        FOR SELECT
        USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert their own transaction edit history"
        ON transaction_edit_history
        FOR INSERT
        WITH CHECK (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    """Remove merchant_name and edit tracking"""

    # Drop RLS policies
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert their own transaction edit history" ON transaction_edit_history;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view their own transaction edit history" ON transaction_edit_history;'
    )

    # Drop trigger and function
    op.execute(
        "DROP TRIGGER IF EXISTS trigger_transaction_edit_tracking ON transactions;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_transaction_edit_tracking();")

    # Drop edit history table and its indices
    op.drop_index("idx_edit_history_edited_at", table_name="transaction_edit_history")
    op.drop_index("idx_edit_history_user", table_name="transaction_edit_history")
    op.drop_index("idx_edit_history_transaction", table_name="transaction_edit_history")
    op.drop_table("transaction_edit_history")

    # Drop transaction table indices
    op.drop_index("idx_transactions_user_edited", table_name="transactions")
    op.drop_index("idx_transactions_merchant_name", table_name="transactions")

    # Drop transaction table columns
    op.drop_column("transactions", "edit_count")
    op.drop_column("transactions", "last_edited_at")
    op.drop_column("transactions", "user_edited_fields")
    op.drop_column("transactions", "original_category")
    op.drop_column("transactions", "original_merchant_name")
    op.drop_column("transactions", "merchant_name")
