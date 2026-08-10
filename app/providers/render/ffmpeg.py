import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from app.models.video_render import (
    RenderAudioCodec,
    RenderFitMode,
    RenderOutputFormat,
    RenderTimelineItemType,
    VideoCodec,
)
from app.schemas.video_render import RenderTimelineItem, VideoRenderInput, VideoRenderResult


class UnsupportedRenderConfigurationError(ValueError):
    """Raised when the local renderer cannot honor the requested format or codecs."""


class MissingRenderInputArtifactError(FileNotFoundError):
    """Raised when a required local input artifact is unavailable or unsafe."""


class RenderArtifactValidationError(RuntimeError):
    """Raised when FFmpeg did not produce a usable video artifact."""


class FFmpegRenderError(RuntimeError):
    """Raised when FFmpeg or ffprobe execution fails."""


class FFmpegVideoRenderer:
    """Render a deterministic local MP4 using FFmpeg.

    The adapter always creates a solid-color base. It optionally overlays the
    first timeline B-roll item whose storage key resolves to a readable local
    file. Metadata-only B-roll is intentionally skipped without network access.
    """

    _MAX_ERROR_LENGTH = 1200
    _LOUDNORM_MIN_DURATION_SECONDS = 1.0

    def __init__(
        self,
        storage_root: str | Path,
        *,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
    ) -> None:
        self.storage_root = Path(storage_root).resolve()
        self.ffmpeg_binary = ffmpeg_binary
        self.ffprobe_binary = ffprobe_binary

    async def render(self, render_input: VideoRenderInput) -> VideoRenderResult:
        snapshot = VideoRenderInput.model_validate(render_input.model_dump(mode="json"))
        return await asyncio.to_thread(self._render_sync, snapshot)

    def _render_sync(self, render_input: VideoRenderInput) -> VideoRenderResult:
        self._validate_configuration(render_input)
        voice_path = self._required_input(render_input.voice_storage_key)
        output_path = self._output_path(render_input.output_storage_key, ".mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        broll = self._first_usable_broll(render_input.timeline)

        with tempfile.TemporaryDirectory(prefix="clipforge-render-") as temporary_root:
            subtitle_path = self._write_subtitles(render_input, Path(temporary_root))
            command = self._build_command(
                render_input,
                voice_path=voice_path,
                output_path=output_path,
                subtitle_path=subtitle_path,
                broll=broll,
            )
            self._run(command)

        duration, streams = self._probe(output_path)
        self._validate_artifact(output_path, duration, streams)
        video_bytes = output_path.read_bytes()
        return VideoRenderResult(
            storage_key=render_input.output_storage_key,
            duration_seconds=duration,
            file_size_bytes=len(video_bytes),
            checksum=f"sha256:{hashlib.sha256(video_bytes).hexdigest()}",
            timeline=render_input.timeline,
            metadata_data={
                "renderer": "ffmpeg",
                "output_format": render_input.render_options.output_format.value,
                "video_codec": render_input.render_options.video_codec.value,
                "audio_codec": render_input.render_options.audio_codec.value,
                "width": render_input.render_options.width,
                "height": render_input.render_options.height,
                "fps": render_input.render_options.fps,
                "subtitles_rendered": subtitle_path is not None,
                "broll_items_rendered": 1 if broll is not None else 0,
            },
        )

    @staticmethod
    def _validate_configuration(render_input: VideoRenderInput) -> None:
        options = render_input.render_options
        supported = (
            options.output_format == RenderOutputFormat.MP4
            and options.video_codec == VideoCodec.H264
            and options.audio_codec == RenderAudioCodec.AAC
        )
        if not supported:
            raise UnsupportedRenderConfigurationError(
                "FFmpegVideoRenderer supports MP4 with H.264 video and AAC audio only"
            )

    def _required_input(self, storage_key: str) -> Path:
        path = self._resolve_key(storage_key)
        if not path.is_file() or not os.access(path, os.R_OK):
            raise MissingRenderInputArtifactError(
                f"Required render input artifact is unavailable: {storage_key}"
            )
        return path

    def _output_path(self, storage_key: str, expected_suffix: str) -> Path:
        path = self._resolve_key(storage_key)
        if path.suffix.casefold() != expected_suffix:
            raise UnsupportedRenderConfigurationError(
                f"Output storage key must end with {expected_suffix}"
            )
        return path

    def _resolve_key(self, storage_key: str) -> Path:
        key_path = Path(storage_key)
        if key_path.is_absolute():
            raise MissingRenderInputArtifactError("Absolute storage keys are not allowed")
        resolved = (self.storage_root / key_path).resolve()
        if not resolved.is_relative_to(self.storage_root):
            raise MissingRenderInputArtifactError("Storage key escapes the configured root")
        return resolved

    def _first_usable_broll(
        self, timeline: list[RenderTimelineItem]
    ) -> tuple[RenderTimelineItem, Path] | None:
        for item in timeline:
            if item.item_type not in {
                RenderTimelineItemType.BROLL_IMAGE,
                RenderTimelineItemType.BROLL_VIDEO,
            }:
                continue
            if item.source_storage_key is None:
                continue
            path = self._resolve_key(item.source_storage_key)
            if path.is_file() and os.access(path, os.R_OK):
                return item, path
        return None

    def _write_subtitles(self, render_input: VideoRenderInput, temporary_root: Path) -> Path | None:
        if not render_input.render_options.subtitle_enabled:
            return None
        subtitles = [
            item
            for item in render_input.timeline
            if item.item_type == RenderTimelineItemType.SUBTITLE and item.text
        ]
        if not subtitles:
            return None
        subtitle_path = temporary_root / "subtitles.srt"
        entries = []
        for index, item in enumerate(subtitles, start=1):
            text = " ".join((item.text or "").split()).replace("-->", "→")
            entries.append(
                f"{index}\n{self._srt_time(item.timeline_start_time)} --> "
                f"{self._srt_time(item.timeline_end_time)}\n{text}\n"
            )
        subtitle_path.write_text("\n".join(entries), encoding="utf-8")
        return subtitle_path

    def _build_command(
        self,
        render_input: VideoRenderInput,
        *,
        voice_path: Path,
        output_path: Path,
        subtitle_path: Path | None,
        broll: tuple[RenderTimelineItem, Path] | None,
    ) -> list[str]:
        options = render_input.render_options
        duration = render_input.voice_duration_seconds
        color = options.background_color.replace("#", "0x")
        command = [
            self.ffmpeg_binary,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={options.width}x{options.height}:r={options.fps}:d={duration}",
            "-i",
            str(voice_path),
        ]
        if broll is not None:
            item, path = broll
            if item.item_type == RenderTimelineItemType.BROLL_IMAGE:
                command.extend(["-loop", "1", "-t", str(duration), "-i", str(path)])
            else:
                command.extend(["-stream_loop", "-1", "-i", str(path)])

        filters = self._video_filters(render_input, subtitle_path, broll)
        audio_filter = self._audio_filter(render_input)
        command.extend(
            [
                "-filter_complex",
                ";".join([*filters, audio_filter]),
                "-map",
                "[video]",
                "-map",
                "[audio]",
                "-t",
                str(duration),
                "-r",
                str(options.fps),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    @classmethod
    def _audio_filter(cls, render_input: VideoRenderInput) -> str:
        options = render_input.render_options
        if not options.normalize_audio:
            return "[1:a]anull[audio]"
        if render_input.voice_duration_seconds < cls._LOUDNORM_MIN_DURATION_SECONDS:
            # Single-pass loudnorm can emit NaN samples when a very short clip has
            # insufficient measurable loudness (notably silence). Resampling and
            # padding preserves the narration while giving AAC a finite frame queue.
            return "[1:a]aresample=48000,apad[audio]"
        return (
            f"[1:a]loudnorm=I={options.target_loudness_lufs}:TP=-1.5:LRA=11,"
            "aresample=48000,apad[audio]"
        )

    def _video_filters(
        self,
        render_input: VideoRenderInput,
        subtitle_path: Path | None,
        broll: tuple[RenderTimelineItem, Path] | None,
    ) -> list[str]:
        options = render_input.render_options
        filters: list[str] = []
        if broll is None:
            filters.append("[0:v]null[visual]")
        else:
            item, _ = broll
            scale = self._scale_filter(
                options.fit_mode,
                options.width,
                options.height,
                options.background_color,
            )
            filters.append(f"[2:v]{scale},setpts=PTS-STARTPTS+{item.timeline_start_time}/TB[broll]")
            filters.append(
                "[0:v][broll]overlay=0:0:"
                f"enable='between(t,{item.timeline_start_time},{item.timeline_end_time})'[visual]"
            )
        if subtitle_path is None:
            filters.append("[visual]null[video]")
        else:
            escaped_path = self._escape_filter_path(subtitle_path)
            style = self._subtitle_force_style(render_input)
            filters.append(f"[visual]subtitles='{escaped_path}':force_style='{style}'[video]")
        return filters

    @staticmethod
    def _scale_filter(mode: RenderFitMode, width: int, height: int, color: str) -> str:
        if mode == RenderFitMode.COVER:
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
            )
        if mode == RenderFitMode.CONTAIN:
            ffmpeg_color = color.replace("#", "0x")
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={ffmpeg_color}"
            )
        return f"scale={width}:{height}"

    @classmethod
    def _subtitle_force_style(cls, render_input: VideoRenderInput) -> str:
        style = render_input.render_options.subtitle_style
        alignment = {"bottom": 2, "center": 5, "top": 8}[style.position]
        return ",".join(
            [
                f"FontName={style.font_family}",
                f"FontSize={style.font_size}",
                f"Bold={-1 if style.font_weight >= 700 else 0}",
                f"PrimaryColour={cls._ass_color(style.text_color)}",
                f"BackColour={cls._ass_color(style.background_color)}",
                f"OutlineColour={cls._ass_color(style.stroke_color)}",
                f"Outline={style.stroke_width}",
                f"Alignment={alignment}",
            ]
        )

    def _probe(self, output_path: Path) -> tuple[float, list[dict]]:
        result = self._run(
            [
                self.ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(output_path),
            ]
        )
        try:
            payload = json.loads(result.stdout)
            return float(payload["format"]["duration"]), payload.get("streams", [])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RenderArtifactValidationError("ffprobe returned invalid artifact data") from error

    @staticmethod
    def _validate_artifact(path: Path, duration: float, streams: list[dict]) -> None:
        if not path.is_file() or path.stat().st_size <= 0 or duration <= 0:
            raise RenderArtifactValidationError("Rendered video artifact is empty or invalid")
        stream_types = {stream.get("codec_type") for stream in streams}
        if not {"video", "audio"} <= stream_types:
            raise RenderArtifactValidationError(
                "Rendered artifact requires video and audio streams"
            )

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            message = (error.stderr or str(error)).strip()[-self._MAX_ERROR_LENGTH :]
            raise FFmpegRenderError(f"FFmpeg command failed: {message}") from error
        except OSError as error:
            raise FFmpegRenderError(f"Unable to execute FFmpeg tooling: {error}") from error

    @staticmethod
    def _srt_time(seconds: float) -> str:
        milliseconds = round(seconds * 1000)
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    @staticmethod
    def _ass_color(color: str) -> str:
        red, green, blue = color[1:3], color[3:5], color[5:7]
        return f"&H00{blue}{green}{red}"
