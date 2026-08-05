"""create B-roll retrieval tables

Revision ID: 20260805_0005
Revises: 20260805_0004
Create Date: 2026-08-05
"""

import sqlalchemy as sa
from alembic import op

revision = "20260805_0005"
down_revision = "20260805_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broll_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("query_strategy", sa.String(length=128), nullable=False),
        sa.Column("retrieval_options", sa.JSON(), nullable=False),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'searching', 'completed', 'failed')",
            name="ck_broll_collections_status",
        ),
        sa.CheckConstraint(
            "provider IN ('local', 'pexels', 'pixabay', 'unsplash', 'other')",
            name="ck_broll_collections_provider",
        ),
        sa.CheckConstraint(
            "length(trim(query_strategy)) > 0",
            name="ck_broll_collections_query_strategy_non_empty",
        ),
        sa.ForeignKeyConstraint(["script_id"], ["scripts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broll_collections_script_id", "broll_collections", ["script_id"])
    op.create_index("ix_broll_collections_status", "broll_collections", ["status"])

    op.create_table(
        "broll_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.Integer(), nullable=False),
        sa.Column("script_section_order", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=256), nullable=True),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("query", sa.String(length=512), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("preview_url", sa.Text(), nullable=True),
        sa.Column("download_url", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=1024), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("checksum", sa.String(length=256), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("license_name", sa.String(length=256), nullable=True),
        sa.Column("photographer_or_creator", sa.String(length=512), nullable=True),
        sa.Column("orientation", sa.String(length=32), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("metadata_data", sa.JSON(), nullable=False),
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
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('candidate', 'selected', 'downloaded', 'rejected', 'failed')",
            name="ck_broll_assets_status",
        ),
        sa.CheckConstraint(
            "provider IN ('local', 'pexels', 'pixabay', 'unsplash', 'other')",
            name="ck_broll_assets_provider",
        ),
        sa.CheckConstraint(
            "media_type IN ('video', 'image')",
            name="ck_broll_assets_media_type",
        ),
        sa.CheckConstraint(
            "orientation IN ('portrait', 'landscape', 'square', 'unknown')",
            name="ck_broll_assets_orientation",
        ),
        sa.CheckConstraint(
            "script_section_order IS NULL OR script_section_order >= 0",
            name="ck_broll_assets_section_order_non_negative",
        ),
        sa.CheckConstraint(
            "length(trim(query)) > 0",
            name="ck_broll_assets_query_non_empty",
        ),
        sa.CheckConstraint(
            "external_id IS NULL OR length(trim(external_id)) > 0",
            name="ck_broll_assets_external_id_non_empty",
        ),
        sa.CheckConstraint(
            "source_url IS NULL OR length(trim(source_url)) > 0",
            name="ck_broll_assets_source_url_non_empty",
        ),
        sa.CheckConstraint(
            "preview_url IS NULL OR length(trim(preview_url)) > 0",
            name="ck_broll_assets_preview_url_non_empty",
        ),
        sa.CheckConstraint(
            "download_url IS NULL OR length(trim(download_url)) > 0",
            name="ck_broll_assets_download_url_non_empty",
        ),
        sa.CheckConstraint(
            "width IS NULL OR width > 0",
            name="ck_broll_assets_width_positive",
        ),
        sa.CheckConstraint(
            "height IS NULL OR height > 0",
            name="ck_broll_assets_height_positive",
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0",
            name="ck_broll_assets_duration_non_negative",
        ),
        sa.CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes >= 0",
            name="ck_broll_assets_file_size_non_negative",
        ),
        sa.CheckConstraint(
            "relevance_score IS NULL OR relevance_score BETWEEN 0.0 AND 1.0",
            name="ck_broll_assets_relevance_range",
        ),
        sa.CheckConstraint(
            "checksum IS NULL OR length(trim(checksum)) > 0",
            name="ck_broll_assets_checksum_non_empty",
        ),
        sa.CheckConstraint(
            "status != 'downloaded' OR (storage_key IS NOT NULL AND length(trim(storage_key)) > 0)",
            name="ck_broll_assets_downloaded_storage",
        ),
        sa.ForeignKeyConstraint(["collection_id"], ["broll_collections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broll_assets_collection_id", "broll_assets", ["collection_id"])
    op.create_index(
        "ix_broll_assets_script_section_order",
        "broll_assets",
        ["script_section_order"],
    )
    op.create_index("ix_broll_assets_status", "broll_assets", ["status"])


def downgrade() -> None:
    op.drop_index("ix_broll_assets_status", table_name="broll_assets")
    op.drop_index("ix_broll_assets_script_section_order", table_name="broll_assets")
    op.drop_index("ix_broll_assets_collection_id", table_name="broll_assets")
    op.drop_table("broll_assets")
    op.drop_index("ix_broll_collections_status", table_name="broll_collections")
    op.drop_index("ix_broll_collections_script_id", table_name="broll_collections")
    op.drop_table("broll_collections")
