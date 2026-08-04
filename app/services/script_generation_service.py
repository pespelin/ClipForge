from datetime import UTC, datetime

from pydantic import ValidationError

from app.core.exceptions import (
    CompletedAnalysisNotFoundError,
    InvalidClipCandidateIndexError,
    InvalidHookCandidateIndexError,
    ScriptGenerationError,
    ScriptNotFoundError,
    TranscriptNotReadyError,
    VideoNotFoundError,
)
from app.models.script import Script, ScriptStatus
from app.models.video import Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.providers.script import ScriptGenerator
from app.repositories.script_repository import ScriptRepository
from app.repositories.video_analysis_repository import VideoAnalysisRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.script import (
    ScriptGenerationOptions,
    ScriptGenerationResult,
    ScriptGeneratorInput,
)
from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import VideoAnalysisResult


class ScriptGenerationService:
    def __init__(
        self,
        video_repository: VideoRepository,
        analysis_repository: VideoAnalysisRepository,
        script_repository: ScriptRepository,
        generator: ScriptGenerator,
    ) -> None:
        self.video_repository = video_repository
        self.analysis_repository = analysis_repository
        self.script_repository = script_repository
        self.generator = generator

    async def create_script(self, video_id: str, options: ScriptGenerationOptions) -> Script:
        validated_options = ScriptGenerationOptions.model_validate(options)
        await self._get_video(video_id)
        analysis = await self._get_completed_analysis_for_video(video_id)
        self._validate_candidate_indexes(analysis, validated_options)
        return await self.script_repository.create(
            Script(
                video_id=video_id,
                video_analysis_id=analysis.id,
                status=ScriptStatus.PENDING,
                target_duration_seconds=validated_options.target_duration_seconds,
                tone=validated_options.tone,
                language=validated_options.language,
                generation_options=validated_options.model_dump(mode="json"),
                sections=[],
            )
        )

    async def process_script(self, script_id: int) -> Script:
        script = await self.get_script(script_id)
        if script.status == ScriptStatus.COMPLETED:
            return script

        video = await self._get_video(script.video_id)
        analysis = await self.analysis_repository.get(script.video_analysis_id)
        analysis = self._verify_linked_analysis(script, analysis)
        self._verify_transcript(video)

        script.status = ScriptStatus.GENERATING
        script.completed_at = None
        script.error_message = None
        await self.script_repository.save(script)

        try:
            generation_input = self._build_generation_input(script, video, analysis)
            raw_result = await self.generator.generate(generation_input)
            result = ScriptGenerationResult.model_validate(raw_result)
            self._apply_result(script, result)
            script.status = ScriptStatus.COMPLETED
            script.completed_at = datetime.now(UTC)
            script.error_message = None
            return await self.script_repository.save(script)
        except Exception as error:
            script.status = ScriptStatus.FAILED
            script.completed_at = None
            script.error_message = self._error_message(error)
            await self.script_repository.save(script)
            raise ScriptGenerationError from error

    async def get_script(self, script_id: int) -> Script:
        script = await self.script_repository.get(script_id)
        if script is None:
            raise ScriptNotFoundError
        return script

    async def list_scripts_for_video(self, video_id: str) -> list[Script]:
        await self._get_video(video_id)
        return await self.script_repository.get_by_video_id(video_id)

    async def _get_video(self, video_id: str) -> Video:
        video = await self.video_repository.get(video_id)
        if video is None:
            raise VideoNotFoundError
        return video

    async def _get_completed_analysis_for_video(self, video_id: str) -> VideoAnalysis:
        analysis = await self.analysis_repository.get_by_video_id(video_id)
        if analysis is None or analysis.status != AnalysisStatus.COMPLETED:
            raise CompletedAnalysisNotFoundError
        return analysis

    @staticmethod
    def _verify_linked_analysis(script: Script, analysis: VideoAnalysis | None) -> VideoAnalysis:
        if (
            analysis is None
            or analysis.status != AnalysisStatus.COMPLETED
            or analysis.video_id != script.video_id
        ):
            raise CompletedAnalysisNotFoundError
        return analysis

    @staticmethod
    def _verify_transcript(video: Video) -> None:
        if video.transcript is None or not video.transcript.full_text.strip():
            raise TranscriptNotReadyError

    @staticmethod
    def _validate_candidate_indexes(
        analysis: VideoAnalysis, options: ScriptGenerationOptions
    ) -> None:
        hook_index = options.preferred_hook_candidate_index
        if hook_index is not None and hook_index >= len(analysis.hook_candidates):
            raise InvalidHookCandidateIndexError
        clip_index = options.preferred_clip_candidate_index
        if clip_index is not None and clip_index >= len(analysis.clip_candidates):
            raise InvalidClipCandidateIndexError

    def _build_generation_input(
        self, script: Script, video: Video, analysis: VideoAnalysis
    ) -> ScriptGeneratorInput:
        options = ScriptGenerationOptions.model_validate(script.generation_options)
        analysis_result = VideoAnalysisResult.model_validate(
            {
                "summary": analysis.summary,
                "topics": analysis.topics,
                "keywords": analysis.keywords,
                "sentiment": analysis.sentiment,
                "hook_candidates": analysis.hook_candidates,
                "clip_candidates": analysis.clip_candidates,
            }
        )
        self._validate_candidate_indexes(analysis, options)
        hook_index = options.preferred_hook_candidate_index
        clip_index = options.preferred_clip_candidate_index
        metadata = (
            VideoMetadata.model_validate(video.metadata_data) if video.metadata_data else None
        )
        return ScriptGeneratorInput(
            video_id=video.id,
            video_analysis_id=analysis.id,
            transcript=video.transcript.full_text,
            video_metadata=metadata,
            analysis=analysis_result,
            options=options,
            selected_hook_candidate=(
                analysis_result.hook_candidates[hook_index] if hook_index is not None else None
            ),
            selected_clip_candidate=(
                analysis_result.clip_candidates[clip_index] if clip_index is not None else None
            ),
        )

    @staticmethod
    def _apply_result(script: Script, result: ScriptGenerationResult) -> None:
        script.title = result.title
        script.hook = result.hook
        script.body = result.body
        script.call_to_action = result.call_to_action
        script.full_script = result.full_script
        script.estimated_duration_seconds = result.estimated_duration_seconds
        script.sections = [section.model_dump(mode="json") for section in result.sections]

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "Script generator returned an invalid structured result"
        return str(error).strip() or type(error).__name__
