"""Merge Alembic heads for device and API key branches

Revision ID: 040_merge_heads_device_and_api
Revises: 039_enforce_trusted_device_client_identity, 036_fix_budget_funcs_and_devices
Create Date: 2026-02-20
"""

# revision identifiers, used by Alembic.
revision = "040_merge_heads_device_and_api"
down_revision = (
    "039_enforce_trusted_device_client_identity",
    "036_fix_budget_funcs_and_devices",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge revision only: schema changes are handled in parent branches.
    pass


def downgrade() -> None:
    # Merge revision only: no direct schema changes to revert.
    pass
