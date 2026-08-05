import hashlib
import math
import re
import struct
import wave
from pathlib import Path

from app.models.voice_track import AudioFormat, VoiceStyle
from app.schemas.script import ScriptSection
from app.schemas.voice_track import TTSInput, TTSResult, VoiceSegment

TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
MAX_AMPLITUDE = 0.75
BASE_AMPLITUDE = 0.18

STYLE_PARAMETERS: dict[VoiceStyle, tuple[float, float, float]] = {
    VoiceStyle.NEUTRAL: (220.0, 0.070, 0.025),
    VoiceStyle.CONVERSATIONAL: (235.0, 0.066, 0.022),
    VoiceStyle.ENERGETIC: (280.0, 0.055, 0.015),
    VoiceStyle.CALM: (190.0, 0.085, 0.035),
    VoiceStyle.DRAMATIC: (165.0, 0.095, 0.045),
    VoiceStyle.INSPIRATIONAL: (250.0, 0.075, 0.030),
}


class UnsupportedAudioFormatError(ValueError):
    """Raised when the local adapter cannot encode the requested audio format."""


class UnsupportedTTSProviderError(ValueError):
    """Raised when an input targets a provider other than the local adapter."""


class UnsupportedTTSLanguageError(ValueError):
    """Raised when the local adapter is asked to synthesize non-English text."""


class UnusableTTSInputError(ValueError):
    """Raised when the input contains no meaningful text to synthesize."""


class LocalTTSProvider:
    """Generate deterministic synthetic PCM WAV artifacts for development.

    Voice identifiers are accepted as stable labels and hashed into a small
    frequency offset. The result is intentionally tonal, not human speech.
    """

    def __init__(self, storage_root: str | Path) -> None:
        self.storage_root = Path(storage_root)

    async def synthesize(self, synthesis_input: TTSInput) -> TTSResult:
        self._validate_input(synthesis_input)
        sections = self._sections(synthesis_input)
        sample_rate = max(8000, min(192000, synthesis_input.sample_rate_hz))
        speaking_rate = max(0.5, min(2.0, synthesis_input.speaking_rate))
        pitch = max(-20.0, min(20.0, synthesis_input.pitch))
        volume_gain_db = max(-60.0, min(20.0, synthesis_input.volume_gain_db))

        frames = bytearray()
        segments: list[VoiceSegment] = []
        has_script_sections = bool(synthesis_input.script_sections)
        section_pause = self._seconds_to_samples(0.045 / speaking_rate, sample_rate)

        for index, section in enumerate(sections):
            start_sample = len(frames) // 2
            frames.extend(
                self._synthesize_text(
                    section.text,
                    sample_rate=sample_rate,
                    speaking_rate=speaking_rate,
                    pitch=pitch,
                    volume_gain_db=volume_gain_db,
                    style=synthesis_input.style,
                    voice=synthesis_input.voice,
                )
            )
            end_sample = len(frames) // 2
            segments.append(
                VoiceSegment(
                    order=index,
                    section_type=section.type,
                    text=section.text,
                    audio_start_time=start_sample / sample_rate,
                    audio_end_time=end_sample / sample_rate,
                    source_script_section_order=section.order if has_script_sections else None,
                )
            )
            if index < len(sections) - 1:
                frames.extend(b"\x00\x00" * section_pause)

        storage_key = f"voice/{synthesis_input.voice_track_id}/audio.wav"
        artifact_path = self.storage_root / storage_key
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_wav(artifact_path, frames, sample_rate)

        audio_bytes = artifact_path.read_bytes()
        duration_seconds = (len(frames) // 2) / sample_rate
        return TTSResult(
            storage_key=storage_key,
            duration_seconds=duration_seconds,
            file_size_bytes=len(audio_bytes),
            checksum=f"sha256:{hashlib.sha256(audio_bytes).hexdigest()}",
            segments=segments,
        )

    @staticmethod
    def _validate_input(synthesis_input: TTSInput) -> None:
        if synthesis_input.provider.casefold() != "local":
            raise UnsupportedTTSProviderError(
                f"LocalTTSProvider cannot handle provider {synthesis_input.provider}"
            )
        if synthesis_input.audio_format != AudioFormat.WAV:
            raise UnsupportedAudioFormatError(
                f"LocalTTSProvider cannot encode {synthesis_input.audio_format.value}"
            )
        if synthesis_input.language.split("-", maxsplit=1)[0].casefold() != "en":
            raise UnsupportedTTSLanguageError(
                f"LocalTTSProvider supports English only, not {synthesis_input.language}"
            )
        normalized = LocalTTSProvider._normalize_text(synthesis_input.full_script)
        if not normalized or not any(character.isalnum() for character in normalized):
            raise UnusableTTSInputError("TTS input contains no meaningful text")

    @classmethod
    def _sections(cls, synthesis_input: TTSInput) -> list[ScriptSection]:
        if synthesis_input.script_sections:
            return [
                section.model_copy(update={"text": cls._normalize_text(section.text)})
                for section in synthesis_input.script_sections
            ]
        return [
            ScriptSection(
                order=0,
                type="full_script",
                text=cls._normalize_text(synthesis_input.full_script),
                estimated_duration_seconds=None,
                source_start_time=None,
                source_end_time=None,
            )
        ]

    @classmethod
    def _synthesize_text(
        cls,
        text: str,
        *,
        sample_rate: int,
        speaking_rate: float,
        pitch: float,
        volume_gain_db: float,
        style: VoiceStyle,
        voice: str,
    ) -> bytes:
        normalized = cls._normalize_text(text)
        tokens = TOKEN_PATTERN.findall(normalized.casefold()) or [normalized]
        base_frequency, tone_seconds, pause_seconds = STYLE_PARAMETERS[style]
        tone_samples = cls._seconds_to_samples(tone_seconds / speaking_rate, sample_rate)
        pause_samples = cls._seconds_to_samples(pause_seconds / speaking_rate, sample_rate)
        voice_offset = cls._hash_fraction(voice) * 80.0 - 40.0
        pitch_multiplier = 2 ** (pitch / 12.0)
        amplitude = min(MAX_AMPLITUDE, BASE_AMPLITUDE * (10 ** (volume_gain_db / 20.0)))

        frames = bytearray()
        for index, token in enumerate(tokens):
            token_offset = cls._hash_fraction(token) * 180.0
            frequency = min(
                (base_frequency + voice_offset + token_offset) * pitch_multiplier,
                sample_rate * 0.4,
            )
            frames.extend(cls._tone(frequency, tone_samples, sample_rate, amplitude))
            if index < len(tokens) - 1:
                frames.extend(b"\x00\x00" * pause_samples)
        return bytes(frames)

    @staticmethod
    def _tone(frequency: float, samples: int, sample_rate: int, amplitude: float) -> bytes:
        frames = bytearray()
        peak = int(32767 * amplitude)
        envelope_samples = max(1, min(samples // 4, int(sample_rate * 0.005)))
        for index in range(samples):
            attack = min(1.0, (index + 1) / envelope_samples)
            release = min(1.0, (samples - index) / envelope_samples)
            envelope = min(attack, release)
            value = int(peak * envelope * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(struct.pack("<h", max(-32768, min(32767, value))))
        return bytes(frames)

    @staticmethod
    def _write_wav(path: Path, frames: bytes | bytearray, sample_rate: int) -> None:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(frames)

    @staticmethod
    def _hash_fraction(value: str) -> float:
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF

    @staticmethod
    def _seconds_to_samples(seconds: float, sample_rate: int) -> int:
        return max(1, round(seconds * sample_rate))

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split())
