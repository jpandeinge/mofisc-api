# migrations/versions/003_add_rls_policies.py

"""Add RLS policies

Revision ID: 003_add_rls_policies
Revises: 002_create_transactions
Create Date: 2025-02-15
"""

from alembic import op

# revision identifiers
revision = "003_add_rls_policies"
down_revision = "002_create_transactions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable RLS on tables
    op.execute("ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;")

    # Create RLS policies for transactions
    op.execute(
        """
        CREATE POLICY "Service role has full access" ON transactions
        USING (true)
        WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view own transactions" ON transactions
        FOR SELECT USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert own transactions" ON transactions
        FOR INSERT WITH CHECK (
            CASE
                WHEN current_setting('role') = 'service_role' THEN true
                WHEN auth.uid() = user_id THEN true
                ELSE false
            END
        );
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can update own transactions" ON transactions
        FOR UPDATE USING (auth.uid() = user_id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can delete own transactions" ON transactions
        FOR DELETE USING (auth.uid() = user_id);
    """
    )

    # Create RLS policies for user_profiles
    op.execute(
        """
        CREATE POLICY "Service role has full access" ON user_profiles
        USING (true)
        WITH CHECK (true);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can view own profile" ON user_profiles
        FOR SELECT USING (auth.uid() = id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can update own profile" ON user_profiles
        FOR UPDATE USING (auth.uid() = id);
    """
    )

    op.execute(
        """
        CREATE POLICY "Users can insert own profile" ON user_profiles
        FOR INSERT WITH CHECK (auth.uid() = id);
    """
    )

    # Create updated_at trigger for user_profiles
    op.execute(
        """
        CREATE OR REPLACE FUNCTION update_user_profiles_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """
    )

    op.execute(
        """
        CREATE TRIGGER update_user_profiles_updated_at
        BEFORE UPDATE ON user_profiles
        FOR EACH ROW
        EXECUTE FUNCTION update_user_profiles_updated_at();
    """
    )


def downgrade() -> None:
    # Drop RLS policies for transactions
    op.execute(
        'DROP POLICY IF EXISTS "Users can delete own transactions" ON transactions;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can update own transactions" ON transactions;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can insert own transactions" ON transactions;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Users can view own transactions" ON transactions;'
    )
    op.execute('DROP POLICY IF EXISTS "Service role has full access" ON transactions;')

    # Drop RLS policies for user_profiles
    op.execute('DROP POLICY IF EXISTS "Users can insert own profile" ON user_profiles;')
    op.execute('DROP POLICY IF EXISTS "Users can update own profile" ON user_profiles;')
    op.execute('DROP POLICY IF EXISTS "Users can view own profile" ON user_profiles;')
    op.execute('DROP POLICY IF EXISTS "Service role has full access" ON user_profiles;')

    # Drop trigger and function
    op.execute(
        "DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;"
    )
    op.execute("DROP FUNCTION IF EXISTS update_user_profiles_updated_at();")

    # Disable RLS
    op.execute("ALTER TABLE transactions DISABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE user_profiles DISABLE ROW LEVEL SECURITY;")
