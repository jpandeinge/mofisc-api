# migrations/versions/001_initial_setup.py

"""Initial database setup

Revision ID: 001_initial_setup
Revises:
Create Date: 2025-02-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001_initial_setup"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Enable UUID extension
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # NOTE: auth.users table is managed by Supabase and should already exist.
    # We reference it via foreign key constraint in user_profiles table below.
    # Do NOT attempt to create or modify tables in the auth schema.

    # Create user_profiles table
    op.create_table(
        "user_profiles",
        sa.Column("id", postgresql.UUID(), nullable=False),
        sa.Column("user_type", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("profile_picture", sa.String(), nullable=True),
        sa.Column("preferences", postgresql.JSONB(), server_default="{}"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
    # Note: We don't drop the auth.users table as it might be managed by Supabase
