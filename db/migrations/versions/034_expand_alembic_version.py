"""Expand alembic_version.version_num length

Revision ID: 034_expand_alembic_version
Revises: 033_fix_trusted_devices_rls
Create Date: 2026-02-16
"""

from alembic import op

# revision identifiers
revision = "034_expand_alembic_version"
down_revision = "033_fix_trusted_devices_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64);")


def downgrade() -> None:
    op.execute("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(32);")
