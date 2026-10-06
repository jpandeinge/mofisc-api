# migrations/versions/028_add_webauthn_credentials.py

"""Add WebAuthn credentials for biometric authentication

Revision ID: 028_add_webauthn_credentials
Revises: 027_add_device_fingerprinting
Create Date: 2026-02-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "028_add_webauthn_credentials"
down_revision = "027_add_device_fingerprinting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "webauthn_credentials",
        sa.Column(
            "id",
            postgresql.UUID(),
            nullable=False,
            server_default=sa.text("uuid_generate_v4()"),
        ),
        sa.Column("user_id", postgresql.UUID(), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.Column(
            "last_used_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["auth.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "credential_id", name="uq_webauthn_user_credential"
        ),
    )

    # Indexes
    op.create_index(
        "idx_webauthn_credentials_user_id", "webauthn_credentials", ["user_id"]
    )
    op.create_index(
        "idx_webauthn_credentials_user_credential",
        "webauthn_credentials",
        ["user_id", "credential_id"],
    )

    # RLS policies
    op.execute(
        """
        ALTER TABLE webauthn_credentials ENABLE ROW LEVEL SECURITY;

        CREATE POLICY users_select_own_webauthn ON webauthn_credentials
            FOR SELECT
            USING (auth.uid() = user_id);

        CREATE POLICY users_insert_own_webauthn ON webauthn_credentials
            FOR INSERT
            WITH CHECK (auth.uid() = user_id);

        CREATE POLICY users_update_own_webauthn ON webauthn_credentials
            FOR UPDATE
            USING (auth.uid() = user_id);

        CREATE POLICY users_delete_own_webauthn ON webauthn_credentials
            FOR DELETE
            USING (auth.uid() = user_id);
    """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP POLICY IF EXISTS users_select_own_webauthn ON webauthn_credentials;
        DROP POLICY IF EXISTS users_insert_own_webauthn ON webauthn_credentials;
        DROP POLICY IF EXISTS users_update_own_webauthn ON webauthn_credentials;
        DROP POLICY IF EXISTS users_delete_own_webauthn ON webauthn_credentials;
    """
    )

    op.drop_index("idx_webauthn_credentials_user_credential", "webauthn_credentials")
    op.drop_index("idx_webauthn_credentials_user_id", "webauthn_credentials")
    op.drop_table("webauthn_credentials")
