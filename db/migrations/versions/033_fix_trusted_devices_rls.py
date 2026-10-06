"""Fix RLS policies for trusted_devices table

Revision ID: 033_fix_trusted_devices_rls
Revises: 032_add_budget_tracking
Create Date: 2026-02-12

This migration fixes the RLS policies for trusted_devices to allow:
- Users to view their own devices
- Service role to insert/update/delete on behalf of users
"""

from alembic import op

# revision identifiers
revision = "033_fix_trusted_devices_rls"
down_revision = "032_add_budget_tracking"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # First, drop any existing policies that might be causing issues
    op.execute(
        """
        DO $$
        BEGIN
            -- Drop existing policies if they exist
            DROP POLICY IF EXISTS "Users can view their own devices" ON trusted_devices;
            DROP POLICY IF EXISTS "Users can insert their own devices" ON trusted_devices;
            DROP POLICY IF EXISTS "Users can update their own devices" ON trusted_devices;
            DROP POLICY IF EXISTS "Users can delete their own devices" ON trusted_devices;
            DROP POLICY IF EXISTS "Service role has full access" ON trusted_devices;
            DROP POLICY IF EXISTS "trusted_devices_select_policy" ON trusted_devices;
            DROP POLICY IF EXISTS "trusted_devices_insert_policy" ON trusted_devices;
            DROP POLICY IF EXISTS "trusted_devices_update_policy" ON trusted_devices;
            DROP POLICY IF EXISTS "trusted_devices_delete_policy" ON trusted_devices;
        END $$;
    """
    )

    # Ensure RLS is enabled
    op.execute("ALTER TABLE trusted_devices ENABLE ROW LEVEL SECURITY;")

    # Create policies that work with service role
    # Service role bypasses RLS by default, but we need policies for authenticated users

    # Policy for SELECT - users can view their own devices
    op.execute(
        """
        CREATE POLICY "Users can view their own devices"
        ON trusted_devices
        FOR SELECT
        TO authenticated
        USING (auth.uid() = user_id);
    """
    )

    # Policy for INSERT - users can insert their own devices
    op.execute(
        """
        CREATE POLICY "Users can insert their own devices"
        ON trusted_devices
        FOR INSERT
        TO authenticated
        WITH CHECK (auth.uid() = user_id);
    """
    )

    # Policy for UPDATE - users can update their own devices
    op.execute(
        """
        CREATE POLICY "Users can update their own devices"
        ON trusted_devices
        FOR UPDATE
        TO authenticated
        USING (auth.uid() = user_id)
        WITH CHECK (auth.uid() = user_id);
    """
    )

    # Policy for DELETE - users can delete their own devices
    op.execute(
        """
        CREATE POLICY "Users can delete their own devices"
        ON trusted_devices
        FOR DELETE
        TO authenticated
        USING (auth.uid() = user_id);
    """
    )

    # Grant service_role bypass rights explicitly
    op.execute(
        """
        ALTER TABLE trusted_devices FORCE ROW LEVEL SECURITY;
    """
    )


def downgrade() -> None:
    # Drop the policies
    op.execute(
        """
        DROP POLICY IF EXISTS "Users can view their own devices" ON trusted_devices;
        DROP POLICY IF EXISTS "Users can insert their own devices" ON trusted_devices;
        DROP POLICY IF EXISTS "Users can update their own devices" ON trusted_devices;
        DROP POLICY IF EXISTS "Users can delete their own devices" ON trusted_devices;
    """
    )

    # Disable RLS
    op.execute("ALTER TABLE trusted_devices DISABLE ROW LEVEL SECURITY;")
