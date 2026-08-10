import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import (
    BrollCollectionNotFoundError,
    RenderBrollCollectionMismatchError,
    RenderBrollCollectionNotReadyError,
    RenderScriptNotReadyError,
    RenderVoiceTrackMismatchError,
    RenderVoiceTrackNotReadyError,
    ScriptNotFoundError,
    UnusableVideoRenderInputError,
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
)
from app.models.script import Script, ScriptStatus
from app.models.video_render import RenderTimelineItemType, VideoRender, VideoRenderStatus
from app.models.voice_track import VoiceTrack, VoiceTrackStatus
from app.providers.render import VideoRenderer
from app.repositories.broll_repository import BrollAssetRepository, BrollCollectionRepository
from app.repositories.script_repository import ScriptRepository
from app.repositories.video_render_repository import VideoRenderRepository
from app.repositories.voice_track_repository import VoiceTrackRepository
from app.schemas.script import ScriptSection
from app.schemas.video_render import (
    RenderOptions,
    RenderTimelineItem,
    SelectedBrollAssetInput,
    VideoRenderInput,
    VideoRenderResult,
)
from app.schemas.voice_track import VoiceSegment


class VideoRenderService:
    def __init__(
        self,
        script_repository: ScriptRepository,
        voice_track_repository: VoiceTrackRepository,
        collection_repository: BrollCollectionRepository,
        asset_repository: BrollAssetRepository,
        render_repository: VideoRenderRepository,
        renderer: VideoRenderer,
    ) -> None:
        self.script_repository = script_repository
        self.voice_track_repository = voice_track_repository
        self.collection_repository = collection_repository
        self.asset_repository = asset_repository
        self.render_repository = render_repository
        self.renderer = renderer

    async def create_render(
        self,
        script_id: int,
        voice_track_id: int,
        broll_collection_id: int | None,
        options: RenderOptions,
    ) -> VideoRender:
        validated_options = RenderOptions.model_validate(options)
        script = await self._get_script(script_id)
        self._verify_script_ready(script)
        voice_track = await self._get_voice_track(voice_track_id)
        self._verify_voice_track_ready(voice_track, script_id)
        collection = await self._get_optional_collection(broll_collection_id, script_id)
        assets = await self._renderable_assets(collection, validated_options)
        timeline = self._build_timeline(script, voice_track, assets, validated_options)
        options_snapshot = self._json_safe(validated_options.model_dump(mode="json"))

        return await self.render_repository.create(
            VideoRender(
                script_id=script_id,
                voice_track_id=voice_track_id,
                broll_collection_id=broll_collection_id,
                status=VideoRenderStatus.PENDING,
                output_format=validated_options.output_format,
                video_codec=validated_options.video_codec,
                audio_codec=validated_options.audio_codec,
                resolution_preset=validated_options.resolution_preset,
                width=validated_options.width,
                height=validated_options.height,
                fps=validated_options.fps,
                fit_mode=validated_options.fit_mode,
                background_color=validated_options.background_color,
                subtitle_enabled=validated_options.subtitle_enabled,
                subtitle_style=self._json_safe(
                    validated_options.subtitle_style.model_dump(mode="json")
                ),
                render_options=options_snapshot,
                timeline_data=[item.model_dump(mode="json") for item in timeline],
            )
        )

    async def process_render(self, render_id: int) -> VideoRender:
        video_render = await self.get_render(render_id)
        if video_render.status == VideoRenderStatus.COMPLETED:
            return video_render

        script = await self._get_script(video_render.script_id)
        self._verify_script_ready(script)
        voice_track = await self._get_voice_track(video_render.voice_track_id)
        self._verify_voice_track_ready(voice_track, script.id)
        collection = await self._get_optional_collection(
            video_render.broll_collection_id, script.id
        )

        video_render.status = VideoRenderStatus.RENDERING
        video_render.completed_at = None
        video_render.error_message = None
        await self.render_repository.save(video_render)

        try:
            options = RenderOptions.model_validate(video_render.render_options)
            assets = await self._renderable_assets(collection, options)
            timeline = self._build_timeline(script, voice_track, assets, options)
            render_input = self._build_render_input(
                video_render, script, voice_track, assets, timeline, options
            )
            raw_result = await self.renderer.render(render_input)
            result = VideoRenderResult.model_validate(raw_result)
            self._apply_result(video_render, result)
            video_render.status = VideoRenderStatus.COMPLETED
            video_render.completed_at = datetime.now(UTC)
            video_render.error_message = None
            return await self.render_repository.save(video_render)
        except Exception as error:
            video_render.status = VideoRenderStatus.FAILED
            video_render.completed_at = None
            video_render.error_message = self._error_message(error)
            await self.render_repository.save(video_render)
            raise VideoRenderingError from error

    async def get_render(self, render_id: int) -> VideoRender:
        video_render = await self.render_repository.get(render_id)
        if video_render is None:
            raise VideoRenderNotFoundError
        return video_render

    async def list_renders_for_script(self, script_id: int) -> list[VideoRender]:
        await self._get_script(script_id)
        return await self.render_repository.get_by_script_id(script_id)

    async def _get_script(self, script_id: int) -> Script:
        script = await self.script_repository.get(script_id)
        if script is None:
            raise ScriptNotFoundError
        return script

    async def _get_voice_track(self, voice_track_id: int) -> VoiceTrack:
        voice_track = await self.voice_track_repository.get(voice_track_id)
        if voice_track is None:
            raise VoiceTrackNotFoundError
        return voice_track

    async def _get_optional_collection(
        self, collection_id: int | None, script_id: int
    ) -> BrollCollection | None:
        if collection_id is None:
            return None
        collection = await self.collection_repository.get(collection_id)
        if collection is None:
            raise BrollCollectionNotFoundError
        if collection.script_id != script_id:
            raise RenderBrollCollectionMismatchError
        if collection.status != BrollCollectionStatus.COMPLETED:
            raise RenderBrollCollectionNotReadyError
        return collection

    @staticmethod
    def _verify_script_ready(script: Script) -> None:
        if script.status != ScriptStatus.COMPLETED:
            raise RenderScriptNotReadyError
        if script.full_script is None or not script.full_script.strip():
            raise UnusableVideoRenderInputError

    @staticmethod
    def _verify_voice_track_ready(voice_track: VoiceTrack, script_id: int) -> None:
        if voice_track.script_id != script_id:
            raise RenderVoiceTrackMismatchError
        if voice_track.status != VoiceTrackStatus.COMPLETED:
            raise RenderVoiceTrackNotReadyError
        if (
            voice_track.storage_key is None
            or not voice_track.storage_key.strip()
            or voice_track.duration_seconds is None
            or voice_track.duration_seconds <= 0
        ):
            raise RenderVoiceTrackNotReadyError

    async def _renderable_assets(
        self, collection: BrollCollection | None, options: RenderOptions
    ) -> list[BrollAsset]:
        if collection is None or not options.include_broll:
            return []
        assets = await self.asset_repository.get_by_collection_id(collection.id)
        renderable = [
            asset
            for asset in assets
            if asset.status in {BrollAssetStatus.SELECTED, BrollAssetStatus.DOWNLOADED}
        ]
        return sorted(
            renderable,
            key=lambda asset: (
                asset.status != BrollAssetStatus.SELECTED,
                asset.script_section_order is None,
                asset.script_section_order or 0,
                -(asset.relevance_score or 0),
                asset.id,
            ),
        )

    @classmethod
    def _build_timeline(
        cls,
        script: Script,
        voice_track: VoiceTrack,
        assets: list[BrollAsset],
        options: RenderOptions,
    ) -> list[RenderTimelineItem]:
        duration = voice_track.duration_seconds
        if duration is None or duration <= 0 or voice_track.storage_key is None:
            raise UnusableVideoRenderInputError
        segments = cls._voice_segments(voice_track, script, duration)
        items = [
            RenderTimelineItem(
                order=0,
                item_type=RenderTimelineItemType.NARRATION,
                source_storage_key=voice_track.storage_key,
                source_start_time=0,
                source_end_time=duration,
                timeline_start_time=0,
                timeline_end_time=duration,
                text=script.full_script,
                metadata={"voice_track_id": voice_track.id},
            )
        ]

        for asset in assets:
            start, end = cls._section_window(asset.script_section_order, segments, duration)
            source_end = (
                asset.duration_seconds
                if asset.media_type == BrollMediaType.VIDEO
                and asset.duration_seconds is not None
                and asset.duration_seconds > 0
                else None
            )
            items.append(
                RenderTimelineItem(
                    order=len(items),
                    item_type=(
                        RenderTimelineItemType.BROLL_VIDEO
                        if asset.media_type == BrollMediaType.VIDEO
                        else RenderTimelineItemType.BROLL_IMAGE
                    ),
                    script_section_order=asset.script_section_order,
                    broll_asset_id=asset.id,
                    source_storage_key=asset.storage_key,
                    source_start_time=0 if source_end is not None else None,
                    source_end_time=source_end,
                    timeline_start_time=start,
                    timeline_end_time=end,
                    transition="cut",
                    metadata={
                        "provider": asset.provider.value,
                        "external_id": asset.external_id,
                        "source_url": asset.source_url,
                        "download_url": asset.download_url,
                    },
                )
            )

        if options.subtitle_enabled:
            for segment in segments:
                items.append(
                    RenderTimelineItem(
                        order=len(items),
                        item_type=RenderTimelineItemType.SUBTITLE,
                        script_section_order=segment.source_script_section_order,
                        timeline_start_time=segment.audio_start_time,
                        timeline_end_time=segment.audio_end_time,
                        text=segment.text,
                        metadata={"subtitle_style": options.subtitle_style.model_dump(mode="json")},
                    )
                )
        return items

    @staticmethod
    def _voice_segments(
        voice_track: VoiceTrack, script: Script, duration: float
    ) -> list[VoiceSegment]:
        if voice_track.segments:
            return [VoiceSegment.model_validate(segment) for segment in voice_track.segments]
        return [
            VoiceSegment(
                order=0,
                section_type="full_script",
                text=script.full_script or "",
                audio_start_time=0,
                audio_end_time=duration,
                source_script_section_order=None,
            )
        ]

    @staticmethod
    def _section_window(
        section_order: int | None, segments: list[VoiceSegment], duration: float
    ) -> tuple[float, float]:
        for segment in segments:
            if segment.source_script_section_order == section_order:
                return segment.audio_start_time, segment.audio_end_time
        return 0, duration

    @classmethod
    def _build_render_input(
        cls,
        video_render: VideoRender,
        script: Script,
        voice_track: VoiceTrack,
        assets: list[BrollAsset],
        timeline: list[RenderTimelineItem],
        options: RenderOptions,
    ) -> VideoRenderInput:
        script_sections = [
            ScriptSection.model_validate(section).model_dump(mode="json")
            for section in script.sections
        ]
        voice_segments = [
            segment.model_dump(mode="json")
            for segment in cls._voice_segments(
                voice_track, script, voice_track.duration_seconds or 0
            )
        ]
        selected_assets = [cls._selected_asset_input(asset) for asset in assets]
        return VideoRenderInput(
            render_id=video_render.id,
            script_id=script.id,
            voice_track_id=voice_track.id,
            broll_collection_id=video_render.broll_collection_id,
            render_options=options,
            script_full_text=script.full_script or "",
            script_sections=script_sections,
            voice_storage_key=voice_track.storage_key or "",
            voice_duration_seconds=voice_track.duration_seconds or 0,
            voice_segments=voice_segments,
            selected_broll_assets=selected_assets,
            timeline=timeline,
            output_storage_key=(f"renders/{video_render.id}/output.{options.output_format.value}"),
        )

    @classmethod
    def _selected_asset_input(cls, asset: BrollAsset) -> SelectedBrollAssetInput:
        return SelectedBrollAssetInput(
            asset_id=asset.id,
            script_section_order=asset.script_section_order,
            provider=asset.provider,
            external_id=asset.external_id,
            media_type=asset.media_type,
            storage_key=asset.storage_key,
            source_url=asset.source_url,
            download_url=asset.download_url,
            width=asset.width,
            height=asset.height,
            duration_seconds=asset.duration_seconds,
            metadata_data=cls._json_safe(asset.metadata_data),
        )

    @staticmethod
    def _apply_result(video_render: VideoRender, result: VideoRenderResult) -> None:
        video_render.storage_key = result.storage_key
        video_render.duration_seconds = result.duration_seconds
        video_render.file_size_bytes = result.file_size_bytes
        video_render.checksum = result.checksum
        video_render.timeline_data = [item.model_dump(mode="json") for item in result.timeline]

    @staticmethod
    def _json_safe(value: Any) -> Any:
        try:
            return json.loads(json.dumps(value))
        except (TypeError, ValueError) as error:
            raise UnusableVideoRenderInputError from error

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "Renderer returned an invalid structured result"
        return str(error).strip() or type(error).__name__
