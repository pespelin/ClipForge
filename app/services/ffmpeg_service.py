import json
import subprocess
from pathlib import Path

from app.core.exceptions import MediaProcessingError
from app.schemas.video import VideoMetadata


class FFmpegService:
    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise MediaProcessingError from exc

    def extract_metadata(self, video_path: Path) -> VideoMetadata:
        result = self._run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(video_path),
            ]
        )
        try:
            payload = json.loads(result.stdout)
            streams = payload.get("streams", [])
            video = next(stream for stream in streams if stream.get("codec_type") == "video")
            audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
            numerator, denominator = video.get("r_frame_rate", "0/1").split("/", 1)
            fps = float(numerator) / float(denominator) if float(denominator) else None
            format_data = payload.get("format", {})
            return VideoMetadata(
                duration=float(format_data["duration"]) if format_data.get("duration") else None,
                width=video.get("width"),
                height=video.get("height"),
                fps=fps,
                codec=video.get("codec_name"),
                bitrate=int(format_data["bit_rate"]) if format_data.get("bit_rate") else None,
                audio_codec=audio.get("codec_name"),
                file_size=video_path.stat().st_size,
            )
        except (KeyError, TypeError, ValueError, StopIteration, OSError) as exc:
            raise MediaProcessingError from exc

    def extract_audio(self, video_path: Path, audio_path: Path) -> Path:
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(video_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ]
        )
        if not audio_path.exists():
            raise MediaProcessingError
        return audio_path
