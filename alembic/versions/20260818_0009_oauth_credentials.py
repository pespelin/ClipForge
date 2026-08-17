"""create OAuth credentials table

Revision ID: 20260818_0009
Revises: 20260817_0008
Create Date: 2026-08-18
"""

import sqlalchemy as sa
from alembic import op

revision = "20260818_0009"
down_revision = "20260817_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("publishing_account_id", sa.Integer(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_type", sa.String(length=64), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(trim(encrypted_access_token)) > 0",
            name="ck_oauth_credentials_access_token_non_empty",
        ),
        sa.CheckConstraint(
            "encrypted_refresh_token IS NULL OR length(trim(encrypted_refresh_token)) > 0",
            name="ck_oauth_credentials_refresh_token_non_empty",
        ),
        sa.CheckConstraint(
            "token_type IS NULL OR length(trim(token_type)) > 0",
            name="ck_oauth_credentials_token_type_non_empty",
        ),
        sa.CheckConstraint(
            "scope IS NULL OR length(trim(scope)) > 0",
            name="ck_oauth_credentials_scope_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["publishing_account_id"], ["publishing_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publishing_account_id",
            name="uq_oauth_credentials_publishing_account_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("oauth_credentials")
