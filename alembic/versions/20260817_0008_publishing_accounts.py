"""create publishing accounts table

Revision ID: 20260817_0008
Revises: 20260813_0007
Create Date: 2026-08-17
"""

import sqlalchemy as sa
from alembic import op

revision = "20260817_0008"
down_revision = "20260813_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "publishing_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("remote_account_id", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram', 'other')",
            name="ck_publishing_accounts_platform",
        ),
        sa.CheckConstraint(
            "length(trim(remote_account_id)) > 0",
            name="ck_publishing_accounts_remote_account_id_non_empty",
        ),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0",
            name="ck_publishing_accounts_display_name_non_empty",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "platform",
            "remote_account_id",
            name="uq_publishing_accounts_platform_remote_account_id",
        ),
    )
    op.create_index("ix_publishing_accounts_platform", "publishing_accounts", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_publishing_accounts_platform", table_name="publishing_accounts")
    op.drop_table("publishing_accounts")
