"""Reconcile achievements that reached 100 percent while still locked

Revision ID: 043_reconcile_completed_achievements
Revises: 042_fix_activity_xp_level_contract
Create Date: 2026-07-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "043_reconcile_completed_achievements"
down_revision = "042_fix_activity_xp_level_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The completion trigger awards points only on the false-to-true edge.
    # This guarded update is safe to retry and awards each repaired
    # achievement exactly once.
    op.execute(
        """
        UPDATE user_achievements
        SET is_completed = true,
            completed_at = COALESCE(completed_at, updated_at, created_at, now()),
            updated_at = now()
        WHERE is_completed = false
          AND progress >= 100;
        """
    )


def downgrade() -> None:
    # Relocking completed achievements would corrupt progress and rewards for
    # users who legitimately completed them after this migration.
    pass
