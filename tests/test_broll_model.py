from sqlalchemy import inspect

from app.models.broll import (
    BrollAsset,
    BrollAssetStatus,
    BrollCollection,
    BrollCollectionStatus,
    BrollMediaType,
    BrollOrientation,
    BrollProvider,
)
from app.models.script import Script


def test_broll_relationships_are_one_to_many() -> None:
    script_collections = inspect(Script).relationships.broll_collections
    collection_script = inspect(BrollCollection).relationships.script
    collection_assets = inspect(BrollCollection).relationships.assets
    asset_collection = inspect(BrollAsset).relationships.collection

    assert script_collections.uselist is True
    assert "delete-orphan" in script_collections.cascade
    assert collection_script.uselist is False
    assert collection_assets.uselist is True
    assert "delete-orphan" in collection_assets.cascade
    assert asset_collection.uselist is False
    assert script_collections.back_populates == "script"
    assert collection_assets.back_populates == "collection"


def test_script_supports_collection_variants_and_multiple_assets_per_section() -> None:
    script = Script(
        id=1,
        video_id="video-1",
        video_analysis_id=1,
        target_duration_seconds=30,
        language="en",
    )
    first = BrollCollection(script=script, query_strategy="section_keywords")
    second = BrollCollection(script=script, query_strategy="visual_concepts")
    first_asset = BrollAsset(collection=first, script_section_order=0, query="city skyline")
    second_asset = BrollAsset(collection=first, script_section_order=0, query="night skyline")

    assert script.broll_collections == [first, second]
    assert first.assets == [first_asset, second_asset]
    assert first_asset.collection is first
    assert second_asset.script_section_order == first_asset.script_section_order == 0


def test_broll_columns_define_defaults_json_and_constraints() -> None:
    collection_table = BrollCollection.__table__
    asset_table = BrollAsset.__table__
    collection_constraints = {constraint.name for constraint in collection_table.constraints}
    asset_constraints = {constraint.name for constraint in asset_table.constraints}

    assert collection_table.c.status.default.arg is BrollCollectionStatus.PENDING
    assert collection_table.c.provider.default.arg is BrollProvider.LOCAL
    assert collection_table.c.query_strategy.default.arg == "section_keywords"
    assert collection_table.c.retrieval_options.nullable is False
    assert collection_table.c.script_id.unique is not True
    assert asset_table.c.status.default.arg is BrollAssetStatus.CANDIDATE
    assert asset_table.c.provider.default.arg is BrollProvider.LOCAL
    assert asset_table.c.media_type.default.arg is BrollMediaType.VIDEO
    assert asset_table.c.orientation.default.arg is BrollOrientation.UNKNOWN
    assert asset_table.c.metadata_data.nullable is False
    assert asset_table.c.collection_id.unique is not True
    assert {
        "ck_broll_collections_status",
        "ck_broll_collections_provider",
        "ck_broll_collections_query_strategy_non_empty",
    } <= collection_constraints
    assert {
        "ck_broll_assets_status",
        "ck_broll_assets_provider",
        "ck_broll_assets_media_type",
        "ck_broll_assets_orientation",
        "ck_broll_assets_section_order_non_negative",
        "ck_broll_assets_query_non_empty",
        "ck_broll_assets_external_id_non_empty",
        "ck_broll_assets_source_url_non_empty",
        "ck_broll_assets_preview_url_non_empty",
        "ck_broll_assets_download_url_non_empty",
        "ck_broll_assets_width_positive",
        "ck_broll_assets_height_positive",
        "ck_broll_assets_duration_non_negative",
        "ck_broll_assets_file_size_non_negative",
        "ck_broll_assets_relevance_range",
        "ck_broll_assets_checksum_non_empty",
        "ck_broll_assets_downloaded_storage",
    } <= asset_constraints
