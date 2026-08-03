from datetime import UTC, datetime

from pydantic import ValidationError

from app.core.exceptions import (
    AnalysisNotFoundError,
    AnalysisProcessingError,
    TranscriptNotReadyError,
    VideoNotFoundError,
)
from app.models.video import Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis
from app.providers.analysis import VideoAnalyzer
from app.repositories.video_analysis_repository import VideoAnalysisRepository
from app.repositories.video_repository import VideoRepository
from app.schemas.video import VideoMetadata
from app.schemas.video_analysis import VideoAnalysisResult


class VideoAnalysisService:
    def __init__(
        self,
        video_repository: VideoRepository,
        analysis_repository: VideoAnalysisRepository,
        analyzer: VideoAnalyzer,
    ) -> None:
        self.video_repository = video_repository
        self.analysis_repository = analysis_repository
        self.analyzer = analyzer

    async def create_or_get_analysis(self, video_id: str) -> VideoAnalysis:
        await self._get_video(video_id)
        analysis = await self.analysis_repository.get_by_video_id(video_id)
        if analysis is not None:
            return analysis
        return await self.analysis_repository.create(
            VideoAnalysis(video_id=video_id, status=AnalysisStatus.PENDING)
        )

    async def process_analysis(self, video_id: str) -> VideoAnalysis:
        video = await self._get_video(video_id)
        self._verify_transcript_ready(video)

        analysis = await self.analysis_repository.get_by_video_id(video_id)
        if analysis is not None and analysis.status == AnalysisStatus.COMPLETED:
            return analysis
        if analysis is None:
            analysis = await self.analysis_repository.create(
                VideoAnalysis(video_id=video_id, status=AnalysisStatus.PENDING)
            )

        analysis.status = AnalysisStatus.PROCESSING
        analysis.completed_at = None
        analysis.error_message = None
        await self.analysis_repository.save(analysis)

        try:
            metadata = (
                VideoMetadata.model_validate(video.metadata_data) if video.metadata_data else None
            )
            raw_result = await self.analyzer.analyze(
                video.transcript.full_text,
                metadata=metadata,
            )
            result = VideoAnalysisResult.model_validate(raw_result)
            self._apply_result(analysis, result)
            analysis.status = AnalysisStatus.COMPLETED
            analysis.completed_at = datetime.now(UTC)
            analysis.error_message = None
            return await self.analysis_repository.save(analysis)
        except Exception as error:
            analysis.status = AnalysisStatus.FAILED
            analysis.completed_at = None
            analysis.error_message = self._error_message(error)
            await self.analysis_repository.save(analysis)
            raise AnalysisProcessingError from error

    async def get_analysis(self, video_id: str) -> VideoAnalysis:
        analysis = await self.analysis_repository.get_by_video_id(video_id)
        if analysis is None:
            raise AnalysisNotFoundError
        return analysis

    async def _get_video(self, video_id: str) -> Video:
        video = await self.video_repository.get(video_id)
        if video is None:
            raise VideoNotFoundError
        return video

    @staticmethod
    def _verify_transcript_ready(video: Video) -> None:
        if (
            video.status != "completed"
            or video.transcript is None
            or not video.transcript.full_text.strip()
        ):
            raise TranscriptNotReadyError

    @staticmethod
    def _apply_result(analysis: VideoAnalysis, result: VideoAnalysisResult) -> None:
        analysis.summary = result.summary
        analysis.topics = [topic.model_dump(mode="json") for topic in result.topics]
        analysis.keywords = result.keywords
        analysis.sentiment = result.sentiment
        analysis.hook_candidates = [
            candidate.model_dump(mode="json") for candidate in result.hook_candidates
        ]
        analysis.clip_candidates = [
            candidate.model_dump(mode="json") for candidate in result.clip_candidates
        ]

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "Analyzer returned an invalid structured result"
        return str(error).strip() or type(error).__name__
