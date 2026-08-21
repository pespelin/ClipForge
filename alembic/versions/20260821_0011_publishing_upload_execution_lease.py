"""add publishing upload execution lease

Revision ID: 20260821_0011
Revises: 20260820_0010
Create Date: 2026-08-21
"""

import sqlalchemy as sa
from alembic import op

revision = "20260821_0011"
down_revision = "20260820_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publishing_upload_sessions",
        sa.Column("execution_owner", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "publishing_upload_sessions",
        sa.Column("execution_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_publishing_upload_sessions_execution_lease_paired",
        "publishing_upload_sessions",
        "(execution_owner IS NULL AND execution_lease_expires_at IS NULL) OR "
        "(execution_owner IS NOT NULL AND execution_lease_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_publishing_upload_sessions_execution_lease_paired",
        "publishing_upload_sessions",
        type_="check",
    )
    op.drop_column("publishing_upload_sessions", "execution_lease_expires_at")
    op.drop_column("publishing_upload_sessions", "execution_owner")
