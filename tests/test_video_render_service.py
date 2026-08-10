from typing import Any

import pytest

from app.core.exceptions import (
    BrollCollectionNotFoundError,
    RenderBrollCollectionMismatchError,
    RenderBrollCollectionNotReadyError,
    RenderScriptNotReadyError,
    RenderVoiceTrackMismatchError,
    RenderVoiceTrackNotReadyError,
    ScriptNotFoundError,
    VideoRenderingError,
    VideoRenderNotFoundError,
    VoiceTrackNotFoundError,
)
from app.models.broll import (
    BrollAsset,
    BrollAssetStatus,
    BrollCollection,
    BrollCollectionStatus,
    BrollMediaType,
    BrollProvider,
)
from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video_render import (
    VideoRender,
    VideoRenderStatus,
)
from app.models.voice_track import AudioFormat, VoiceStyle, VoiceTrack, VoiceTrackStatus
from app.schemas.video_render import RenderOptions, VideoRenderInput
from app.services.video_render_service import VideoRenderService


class FakeRepository:
    def __init__(self, rows=None) -> None:
        self.rows = {row.id: row for row in rows or []}
        self.created = []
        self.saved_statuses = []

    async def create(self, row):
        row.id = max(self.rows, default=0) + 1
        self.rows[row.id] = row
        self.created.append(row)
        return row

    async def get(self, row_id: int):
        return self.rows.get(row_id)

    async def get_by_script_id(self, script_id: int):
        return [row for row in self.rows.values() if row.script_id == script_id]

    async def save(self, row):
        self.rows[row.id] = row
        self.saved_statuses.append(row.status)
        return row


class FakeAssetRepository(FakeRepository):
    async def get_by_collection_id(self, collection_id: int):
        return [row for row in self.rows.values() if row.collection_id == collection_id]


class RecordingRenderer:
    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.inputs: list[VideoRenderInput] = []

    async def render(self, render_input: VideoRenderInput):
        self.calls += 1
        self.inputs.append(render_input)
        if self.error:
            raise self.error
        return self.result or {
            "storage_key": render_input.output_storage_key,
            "duration_seconds": render_input.voice_duration_seconds,
            "file_size_bytes": 4096,
            "checksum": "sha256:abc",
            "timeline": [item.model_dump(mode="json") for item in render_input.timeline],
            "metadata_data": {"renderer": "fake"},
        }


def completed_script(**values) -> Script:
    defaults = {
        "id": 4,
        "video_id": "video-1",
        "video_analysis_id": 7,
        "status": ScriptStatus.COMPLETED,
        "title": "Editing",
        "hook": "Start strong.",
        "body": "Keep the visual focused.",
        "full_script": "Start strong. Keep the visual focused.",
        "target_duration_seconds": 30,
        "tone": ScriptTone.EDUCATIONAL,
        "language": "en",
        "generation_options": {},
        "sections": [
            {"order": 0, "type": "hook", "text": "Start strong."},
            {"order": 1, "type": "body", "text": "Keep the visual focused."},
        ],
    }
    defaults.update(values)
    return Script(**defaults)


def completed_voice(**values) -> VoiceTrack:
    defaults = {
        "id": 8,
        "script_id": 4,
        "status": VoiceTrackStatus.COMPLETED,
        "provider": "local",
        "voice": "default",
        "style": VoiceStyle.NEUTRAL,
        "language": "en",
        "audio_format": AudioFormat.WAV,
        "sample_rate_hz": 24000,
        "speaking_rate": 1,
        "pitch": 0,
        "volume_gain_db": 0,
        "generation_options": {},
        "segments": [
            {
                "order": 0,
                "section_type": "hook",
                "text": "Start strong.",
                "audio_start_time": 0,
                "audio_end_time": 3,
                "source_script_section_order": 0,
            },
            {
                "order": 1,
                "section_type": "body",
                "text": "Keep the visual focused.",
                "audio_start_time": 3,
                "audio_end_time": 10,
                "source_script_section_order": 1,
            },
        ],
        "storage_key": "voice/8/audio.wav",
        "duration_seconds": 10,
        "file_size_bytes": 100,
    }
    defaults.update(values)
    return VoiceTrack(**defaults)


def completed_collection(**values) -> BrollCollection:
    defaults = {
        "id": 12,
        "script_id": 4,
        "status": BrollCollectionStatus.COMPLETED,
        "provider": BrollProvider.LOCAL,
        "query_strategy": "section_keywords",
        "retrieval_options": {},
    }
    defaults.update(values)
    return BrollCollection(**defaults)


def broll_asset(asset_id: int, status: BrollAssetStatus, section: int = 0) -> BrollAsset:
    return BrollAsset(
        id=asset_id,
        collection_id=12,
        script_section_order=section,
        provider=BrollProvider.LOCAL,
        external_id=f"local-{asset_id}",
        media_type=BrollMediaType.VIDEO,
        status=status,
        query="editing",
        source_url=f"https://local.invalid/{asset_id}",
        width=1080,
        height=1920,
        duration_seconds=4,
        orientation="portrait",
        relevance_score=0.9,
        metadata_data={"synthetic": True},
    )


def make_service(
    *,
    script: Script | None = None,
    voice: VoiceTrack | None = None,
    collection: BrollCollection | None = None,
    assets: list[BrollAsset] | None = None,
    renders: list[VideoRender] | None = None,
    renderer: RecordingRenderer | None = None,
):
    script_repo = FakeRepository([script] if script else [])
    voice_repo = FakeRepository([voice] if voice else [])
    collection_repo = FakeRepository([collection] if collection else [])
    asset_repo = FakeAssetRepository(assets)
    render_repo = FakeRepository(renders)
    renderer = renderer or RecordingRenderer()
    service = VideoRenderService(
        script_repo,
        voice_repo,
        collection_repo,
        asset_repo,
        render_repo,
        renderer,
    )
    return service, render_repo, renderer


async def test_create_pending_render_snapshot_timeline_and_output_inputs() -> None:
    service, renders, _ = make_service(
        script=completed_script(),
        voice=completed_voice(),
        collection=completed_collection(),
        assets=[
            broll_asset(1, BrollAssetStatus.CANDIDATE),
            broll_asset(2, BrollAssetStatus.SELECTED),
            broll_asset(3, BrollAssetStatus.REJECTED),
            broll_asset(4, BrollAssetStatus.DOWNLOADED, section=1),
        ],
    )

    render = await service.create_render(4, 8, 12, RenderOptions())

    assert render.status == VideoRenderStatus.PENDING
    assert render.render_options == RenderOptions().model_dump(mode="json")
    assert render.subtitle_style == RenderOptions().subtitle_style.model_dump(mode="json")
    item_types = [item["item_type"] for item in render.timeline_data]
    assert item_types == ["narration", "broll_video", "broll_video", "subtitle", "subtitle"]
    assert [item["broll_asset_id"] for item in render.timeline_data[1:3]] == [2, 4]
    assert render.timeline_data[1]["timeline_end_time"] == 3
    assert render.timeline_data[2]["timeline_start_time"] == 3
    assert renders.created == [render]


async def test_create_allows_variants_and_no_broll() -> None:
    service, renders, _ = make_service(script=completed_script(), voice=completed_voice())

    first = await service.create_render(4, 8, None, RenderOptions())
    second = await service.create_render(4, 8, None, RenderOptions(subtitle_enabled=False))

    assert first.id != second.id
    assert len(renders.rows) == 2
    assert [item["item_type"] for item in first.timeline_data] == [
        "narration",
        "subtitle",
        "subtitle",
    ]
    assert [item["item_type"] for item in second.timeline_data] == ["narration"]


@pytest.mark.parametrize(
    ("setup", "error_type"),
    [
        ({}, ScriptNotFoundError),
        ({"script": completed_script(status=ScriptStatus.PENDING)}, RenderScriptNotReadyError),
        ({"script": completed_script()}, VoiceTrackNotFoundError),
        (
            {"script": completed_script(), "voice": completed_voice(script_id=99)},
            RenderVoiceTrackMismatchError,
        ),
        (
            {
                "script": completed_script(),
                "voice": completed_voice(status=VoiceTrackStatus.FAILED),
            },
            RenderVoiceTrackNotReadyError,
        ),
        (
            {"script": completed_script(), "voice": completed_voice()},
            BrollCollectionNotFoundError,
        ),
        (
            {
                "script": completed_script(),
                "voice": completed_voice(),
                "collection": completed_collection(script_id=99),
            },
            RenderBrollCollectionMismatchError,
        ),
        (
            {
                "script": completed_script(),
                "voice": completed_voice(),
                "collection": completed_collection(status=BrollCollectionStatus.SEARCHING),
            },
            RenderBrollCollectionNotReadyError,
        ),
    ],
)
async def test_creation_readiness_errors(setup: dict, error_type: type[Exception]) -> None:
    service, _, _ = make_service(**setup)
    collection_id = 12 if "collection" in setup or setup.get("voice") else None
    if (
        setup.get("voice")
        and "collection" not in setup
        and error_type is BrollCollectionNotFoundError
    ):
        collection_id = 12

    with pytest.raises(error_type):
        await service.create_render(4, 8, collection_id, RenderOptions())


async def test_include_broll_false_excludes_all_assets() -> None:
    service, _, _ = make_service(
        script=completed_script(),
        voice=completed_voice(),
        collection=completed_collection(),
        assets=[broll_asset(2, BrollAssetStatus.SELECTED)],
    )

    render = await service.create_render(4, 8, 12, RenderOptions(include_broll=False))

    assert all(item["broll_asset_id"] is None for item in render.timeline_data)


async def test_include_broll_with_only_candidates_continues_without_broll() -> None:
    service, _, _ = make_service(
        script=completed_script(),
        voice=completed_voice(),
        collection=completed_collection(),
        assets=[broll_asset(1, BrollAssetStatus.CANDIDATE)],
    )

    render = await service.create_render(4, 8, 12, RenderOptions(include_broll=True))

    assert [item["item_type"] for item in render.timeline_data] == [
        "narration",
        "subtitle",
        "subtitle",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"storage_key": "   "},
        {"duration_seconds": 0},
        {"duration_seconds": None},
    ],
)
async def test_completed_voice_requires_usable_artifact_metadata(changes: dict) -> None:
    service, _, _ = make_service(script=completed_script(), voice=completed_voice(**changes))

    with pytest.raises(RenderVoiceTrackNotReadyError):
        await service.create_render(4, 8, None, RenderOptions())


async def test_process_maps_result_and_crosses_boundary_with_pydantic_only() -> None:
    renderer = RecordingRenderer()
    service, renders, _ = make_service(
        script=completed_script(),
        voice=completed_voice(),
        collection=completed_collection(),
        assets=[broll_asset(2, BrollAssetStatus.SELECTED)],
        renderer=renderer,
    )
    render = await service.create_render(4, 8, 12, RenderOptions(output_format="webm"))

    result = await service.process_render(render.id)

    assert renders.saved_statuses == [VideoRenderStatus.RENDERING, VideoRenderStatus.COMPLETED]
    assert result.status == VideoRenderStatus.COMPLETED
    assert result.storage_key == f"renders/{render.id}/output.webm"
    assert result.duration_seconds == 10
    assert result.file_size_bytes == 4096
    assert result.checksum == "sha256:abc"
    assert result.completed_at is not None
    assert renderer.calls == 1
    boundary_input = renderer.inputs[0]
    assert isinstance(boundary_input, VideoRenderInput)
    assert boundary_input.output_storage_key.endswith("output.webm")
    assert boundary_input.selected_broll_assets[0].asset_id == 2
    assert isinstance(boundary_input.script_sections[0], dict)
    assert isinstance(boundary_input.voice_segments[0], dict)


async def test_completed_render_is_idempotent() -> None:
    completed = VideoRender(
        id=1,
        script_id=4,
        voice_track_id=8,
        status=VideoRenderStatus.COMPLETED,
        storage_key="renders/1/output.mp4",
        duration_seconds=10,
        file_size_bytes=100,
    )
    renderer = RecordingRenderer(error=AssertionError("renderer must not run"))
    service, _, _ = make_service(renders=[completed], renderer=renderer)

    assert await service.process_render(1) is completed
    assert renderer.calls == 0


@pytest.mark.parametrize(
    ("renderer", "expected_message"),
    [
        (RecordingRenderer(error=RuntimeError("render failed")), "render failed"),
        (
            RecordingRenderer(result={"storage_key": "bad"}),
            "Renderer returned an invalid structured result",
        ),
    ],
)
async def test_renderer_failure_persists_failed_state(
    renderer: RecordingRenderer, expected_message: str
) -> None:
    service, renders, _ = make_service(
        script=completed_script(), voice=completed_voice(), renderer=renderer
    )
    render = await service.create_render(4, 8, None, RenderOptions())

    with pytest.raises(VideoRenderingError) as caught:
        await service.process_render(render.id)

    assert caught.value.__cause__ is not None
    assert render.status == VideoRenderStatus.FAILED
    assert render.completed_at is None
    assert render.error_message == expected_message
    assert renders.saved_statuses == [VideoRenderStatus.RENDERING, VideoRenderStatus.FAILED]


async def test_get_and_list_render_behavior() -> None:
    existing = VideoRender(id=2, script_id=4, voice_track_id=8)
    service, _, _ = make_service(script=completed_script(), renders=[existing])

    assert await service.get_render(2) is existing
    assert await service.list_renders_for_script(4) == [existing]
    with pytest.raises(VideoRenderNotFoundError):
        await service.get_render(999)
