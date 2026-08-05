from datetime import UTC, datetime

from pydantic import ValidationError

from app.core.exceptions import (
    ScriptNotFoundError,
    ScriptNotReadyError,
    UnusableScriptContentError,
    VoiceGenerationError,
    VoiceTrackNotFoundError,
)
from app.models.script import Script, ScriptStatus
from app.models.voice_track import VoiceTrack, VoiceTrackStatus
from app.providers.tts import TTSProvider
from app.repositories.script_repository import ScriptRepository
from app.repositories.voice_track_repository import VoiceTrackRepository
from app.schemas.script import ScriptSection
from app.schemas.voice_track import (
    TTSInput,
    TTSResult,
    VoiceGenerationOptions,
)


class VoiceGenerationService:
    def __init__(
        self,
        script_repository: ScriptRepository,
        voice_track_repository: VoiceTrackRepository,
        tts_provider: TTSProvider,
    ) -> None:
        self.script_repository = script_repository
        self.voice_track_repository = voice_track_repository
        self.tts_provider = tts_provider

    async def create_voice_track(
        self, script_id: int, options: VoiceGenerationOptions
    ) -> VoiceTrack:
        validated_options = VoiceGenerationOptions.model_validate(options)
        script = await self._get_script(script_id)
        self._verify_script_ready(script)
        return await self.voice_track_repository.create(
            VoiceTrack(
                script_id=script_id,
                status=VoiceTrackStatus.PENDING,
                provider=validated_options.provider,
                voice=validated_options.voice,
                style=validated_options.style,
                language=validated_options.language,
                audio_format=validated_options.audio_format,
                sample_rate_hz=validated_options.sample_rate_hz,
                speaking_rate=validated_options.speaking_rate,
                pitch=validated_options.pitch,
                volume_gain_db=validated_options.volume_gain_db,
                generation_options=validated_options.model_dump(mode="json"),
                segments=[],
            )
        )

    async def request_voice_generation(
        self, script_id: int, options: VoiceGenerationOptions
    ) -> VoiceTrack:
        voice_track = await self.create_voice_track(script_id, options)
        await self.voice_track_repository.commit()
        return voice_track

    async def prepare_voice_track_retry(self, voice_track_id: int) -> tuple[VoiceTrack, bool]:
        voice_track = await self.get_voice_track(voice_track_id)
        if voice_track.status in {
            VoiceTrackStatus.COMPLETED,
            VoiceTrackStatus.GENERATING,
        }:
            return voice_track, False

        voice_track.status = VoiceTrackStatus.PENDING
        voice_track.completed_at = None
        voice_track.error_message = None
        await self.voice_track_repository.save(voice_track)
        await self.voice_track_repository.commit()
        return voice_track, True

    async def mark_voice_enqueue_failed(self, voice_track: VoiceTrack, error: Exception) -> None:
        voice_track.status = VoiceTrackStatus.FAILED
        voice_track.completed_at = None
        message = str(error).strip() or type(error).__name__
        voice_track.error_message = f"Voice generation task enqueue failed: {message}"
        await self.voice_track_repository.save(voice_track)
        await self.voice_track_repository.commit()

    async def process_voice_track(self, voice_track_id: int) -> VoiceTrack:
        voice_track = await self.get_voice_track(voice_track_id)
        if voice_track.status == VoiceTrackStatus.COMPLETED:
            return voice_track

        script = await self._get_script(voice_track.script_id)
        self._verify_script_ready(script)

        voice_track.status = VoiceTrackStatus.GENERATING
        voice_track.completed_at = None
        voice_track.error_message = None
        await self.voice_track_repository.save(voice_track)

        try:
            synthesis_input = self._build_tts_input(voice_track, script)
            raw_result = await self.tts_provider.synthesize(synthesis_input)
            result = TTSResult.model_validate(raw_result)
            self._apply_result(voice_track, result)
            voice_track.status = VoiceTrackStatus.COMPLETED
            voice_track.completed_at = datetime.now(UTC)
            voice_track.error_message = None
            return await self.voice_track_repository.save(voice_track)
        except Exception as error:
            voice_track.status = VoiceTrackStatus.FAILED
            voice_track.completed_at = None
            voice_track.error_message = self._error_message(error)
            await self.voice_track_repository.save(voice_track)
            raise VoiceGenerationError from error

    async def get_voice_track(self, voice_track_id: int) -> VoiceTrack:
        voice_track = await self.voice_track_repository.get(voice_track_id)
        if voice_track is None:
            raise VoiceTrackNotFoundError
        return voice_track

    async def list_voice_tracks_for_script(self, script_id: int) -> list[VoiceTrack]:
        await self._get_script(script_id)
        return await self.voice_track_repository.get_by_script_id(script_id)

    async def _get_script(self, script_id: int) -> Script:
        script = await self.script_repository.get(script_id)
        if script is None:
            raise ScriptNotFoundError
        return script

    @staticmethod
    def _verify_script_ready(script: Script) -> None:
        if script.status != ScriptStatus.COMPLETED:
            raise ScriptNotReadyError
        if script.full_script is None or not script.full_script.strip():
            raise UnusableScriptContentError

    @staticmethod
    def _build_tts_input(voice_track: VoiceTrack, script: Script) -> TTSInput:
        VoiceGenerationOptions.model_validate(voice_track.generation_options)
        sections = [ScriptSection.model_validate(section) for section in script.sections]
        return TTSInput(
            voice_track_id=voice_track.id,
            script_id=script.id,
            full_script=script.full_script,
            language=voice_track.language,
            provider=voice_track.provider,
            voice=voice_track.voice,
            style=voice_track.style,
            audio_format=voice_track.audio_format,
            sample_rate_hz=voice_track.sample_rate_hz,
            speaking_rate=voice_track.speaking_rate,
            pitch=voice_track.pitch,
            volume_gain_db=voice_track.volume_gain_db,
            script_sections=sections,
        )

    @staticmethod
    def _apply_result(voice_track: VoiceTrack, result: TTSResult) -> None:
        voice_track.storage_key = result.storage_key
        voice_track.duration_seconds = result.duration_seconds
        voice_track.file_size_bytes = result.file_size_bytes
        voice_track.checksum = result.checksum
        voice_track.segments = [segment.model_dump(mode="json") for segment in result.segments]

    @staticmethod
    def _error_message(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "TTS provider returned an invalid structured result"
        return str(error).strip() or type(error).__name__
