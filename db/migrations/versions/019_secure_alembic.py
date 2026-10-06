"""Secure alembic_version table to prevent information disclosure

Revision ID: 019_secure_alembic
Revises: 018_secure_notifications
Create Date: 2025-12-31
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision = "019_secure_alembic"
down_revision = "018_secure_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Secure alembic_version table to prevent information disclosure.

    Security Rationale:
    - Migration version names can reveal feature roadmap information
    - Exposing version history could help attackers understand security patches
    - This is metadata that regular users have no legitimate need to access
    - Follows fintech security principle of least privilege
    """

    # 1. Enable Row Level Security on alembic_version
    op.execute("ALTER TABLE alembic_version ENABLE ROW LEVEL SECURITY;")

    # 2. Drop any existing policies (for idempotency)
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access to alembic_version" ON alembic_version;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Block all user access to alembic_version" ON alembic_version;'
    )

    # 3. Create policy: Only service_role can access alembic_version
    # Regular authenticated users should NEVER see migration versions
    op.execute(
        """
        CREATE POLICY "Service role has full access to alembic_version" ON alembic_version
        FOR ALL
        USING (current_setting('role') = 'service_role')
        WITH CHECK (current_setting('role') = 'service_role');
    """
    )

    # 4. Create explicit DENY policy for all other users
    # This ensures even if someone tries, they cannot access this table
    op.execute(
        """
        CREATE POLICY "Block all user access to alembic_version" ON alembic_version
        FOR ALL
        USING (false)
        WITH CHECK (false);
    """
    )


def downgrade() -> None:
    """
    Remove security from alembic_version table.
    WARNING: This will expose migration version information.
    """

    # Drop policies
    op.execute(
        'DROP POLICY IF EXISTS "Block all user access to alembic_version" ON alembic_version;'
    )
    op.execute(
        'DROP POLICY IF EXISTS "Service role has full access to alembic_version" ON alembic_version;'
    )

    # Disable RLS
    op.execute("ALTER TABLE alembic_version DISABLE ROW LEVEL SECURITY;")
