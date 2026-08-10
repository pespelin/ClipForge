import hashlib
import inspect
import json
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from app.providers.render import (
    FFmpegRenderError,
    FFmpegVideoRenderer,
    MissingRenderInputArtifactError,
    RenderArtifactValidationError,
    UnsupportedRenderConfigurationError,
    VideoRenderer,
)
from app.schemas.video_render import RenderOptions, VideoRenderInput, VideoRenderResult


def render_input(**values) -> VideoRenderInput:
    options = values.pop(
        "render_options",
        RenderOptions(
            resolution_preset="custom",
            width=160,
            height=90,
            fps=12,
            subtitle_enabled=False,
        ),
    )
    defaults = {
        "render_id": 5,
        "script_id": 4,
        "voice_track_id": 8,
        "broll_collection_id": None,
        "render_options": options,
        "script_full_text": "Render this narration.",
        "script_sections": [],
        "voice_storage_key": "voice/8/audio.wav",
        "voice_duration_seconds": 0.4,
        "voice_segments": [],
        "selected_broll_assets": [],
        "timeline": [
            {
                "order": 0,
                "item_type": "narration",
                "source_storage_key": "voice/8/audio.wav",
                "source_start_time": 0,
                "source_end_time": 0.4,
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
                "text": "Render this narration.",
            }
        ],
        "output_storage_key": "renders/5/output.mp4",
    }
    defaults.update(values)
    return VideoRenderInput.model_validate(defaults)


def write_wav(path: Path, duration: float = 0.4, sample_rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = round(duration * sample_rate)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(struct.pack(f"<{samples}h", *([0] * samples)))


def command_for(
    renderer: FFmpegVideoRenderer,
    provider_input: VideoRenderInput,
    tmp_path: Path,
    *,
    subtitle_path: Path | None = None,
    broll=None,
) -> list[str]:
    return renderer._build_command(
        provider_input,
        voice_path=tmp_path / "voice.wav",
        output_path=tmp_path / "output.mp4",
        subtitle_path=subtitle_path,
        broll=broll,
    )


def test_protocol_compatibility_and_async_boundary(tmp_path: Path) -> None:
    renderer: VideoRenderer = FFmpegVideoRenderer(tmp_path)
    assert isinstance(renderer, FFmpegVideoRenderer)
    assert inspect.iscoroutinefunction(renderer.render)


def test_background_narration_and_codec_command(tmp_path: Path) -> None:
    renderer = FFmpegVideoRenderer(tmp_path)
    command = command_for(renderer, render_input(), tmp_path)
    joined = " ".join(command)

    assert command[:2] == ["ffmpeg", "-y"]
    assert "color=c=0x000000:s=160x90:r=12.0:d=0.4" in joined
    assert str(tmp_path / "voice.wav") in command
    assert "[0:v]null[visual]" in joined
    assert "libx264" in command
    assert "aac" in command
    assert command[-1] == str(tmp_path / "output.mp4")


@pytest.mark.parametrize(
    ("normalize", "duration", "expected", "excluded"),
    [
        (True, 0.4, "[1:a]aresample=48000,apad[audio]", "loudnorm="),
        (True, 2.0, "loudnorm=I=-14.0", None),
        (False, 0.4, "[1:a]anull[audio]", "loudnorm="),
    ],
)
def test_audio_normalization_command(
    normalize: bool,
    duration: float,
    expected: str,
    excluded: str | None,
    tmp_path: Path,
) -> None:
    options = RenderOptions(
        resolution_preset="custom",
        width=160,
        height=90,
        normalize_audio=normalize,
        subtitle_enabled=False,
    )
    command = command_for(
        FFmpegVideoRenderer(tmp_path),
        render_input(render_options=options, voice_duration_seconds=duration),
        tmp_path,
    )
    assert expected in " ".join(command)
    if excluded is not None:
        assert excluded not in " ".join(command)


@pytest.mark.parametrize(
    ("mode", "fragment"),
    [
        ("cover", "force_original_aspect_ratio=increase,crop=160:90"),
        ("contain", "force_original_aspect_ratio=decrease,pad=160:90"),
        ("stretch", "scale=160:90"),
    ],
)
def test_fit_mode_filters(mode: str, fragment: str, tmp_path: Path) -> None:
    options = RenderOptions(
        resolution_preset="custom", width=160, height=90, fit_mode=mode, subtitle_enabled=False
    )
    provider_input = render_input(
        render_options=options,
        timeline=[
            *render_input().model_dump(mode="json")["timeline"],
            {
                "order": 1,
                "item_type": "broll_image",
                "source_storage_key": "broll/image.jpg",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
            },
        ],
    )
    item = provider_input.timeline[1]
    command = command_for(
        FFmpegVideoRenderer(tmp_path), provider_input, tmp_path, broll=(item, tmp_path / "x.jpg")
    )
    assert fragment in " ".join(command)


def test_subtitle_file_and_command_are_deterministic(tmp_path: Path) -> None:
    options = RenderOptions(resolution_preset="custom", width=160, height=90, subtitle_enabled=True)
    provider_input = render_input(
        render_options=options,
        timeline=[
            *render_input().model_dump(mode="json")["timeline"],
            {
                "order": 1,
                "item_type": "subtitle",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
                "text": "Hello   world",
            },
        ],
    )
    renderer = FFmpegVideoRenderer(tmp_path)
    subtitle = renderer._write_subtitles(provider_input, tmp_path)
    assert subtitle is not None
    assert "00:00:00,000 --> 00:00:00,400" in subtitle.read_text()
    assert "Hello world" in subtitle.read_text()
    command = command_for(renderer, provider_input, tmp_path, subtitle_path=subtitle)
    assert "subtitles=" in " ".join(command)
    assert "FontName=Arial" in " ".join(command)


def test_subtitle_disabled_creates_no_file(tmp_path: Path) -> None:
    renderer = FFmpegVideoRenderer(tmp_path)
    assert renderer._write_subtitles(render_input(), tmp_path) is None
    assert list(tmp_path.iterdir()) == []


def test_metadata_only_and_missing_broll_are_skipped(tmp_path: Path) -> None:
    renderer = FFmpegVideoRenderer(tmp_path)
    no_key = render_input(
        timeline=[
            *render_input().model_dump(mode="json")["timeline"],
            {
                "order": 1,
                "item_type": "broll_video",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
                "metadata": {"source_url": "https://local.invalid/video"},
            },
            {
                "order": 2,
                "item_type": "broll_image",
                "source_storage_key": "missing/image.jpg",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
            },
        ]
    )
    assert renderer._first_usable_broll(no_key.timeline) is None


def test_usable_local_broll_is_included(tmp_path: Path) -> None:
    broll_path = tmp_path / "broll/image.jpg"
    broll_path.parent.mkdir(parents=True)
    broll_path.write_bytes(b"image")
    provider_input = render_input(
        timeline=[
            *render_input().model_dump(mode="json")["timeline"],
            {
                "order": 1,
                "item_type": "broll_image",
                "source_storage_key": "broll/image.jpg",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
            },
        ]
    )

    found = FFmpegVideoRenderer(tmp_path)._first_usable_broll(provider_input.timeline)
    assert found == (provider_input.timeline[1], broll_path)


@pytest.mark.parametrize("key", ["../escape.wav", "/tmp/escape.wav"])
def test_path_traversal_and_absolute_keys_are_rejected(tmp_path: Path, key: str) -> None:
    renderer = FFmpegVideoRenderer(tmp_path)
    with pytest.raises(MissingRenderInputArtifactError):
        renderer._resolve_key(key)


def test_missing_voice_artifact_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(MissingRenderInputArtifactError):
        FFmpegVideoRenderer(tmp_path)._render_sync(render_input())


@pytest.mark.parametrize(
    "options",
    [
        RenderOptions(
            output_format="webm",
            video_codec="vp9",
            audio_codec="opus",
            resolution_preset="custom",
            width=160,
            height=90,
        ),
        RenderOptions(
            output_format="mp4",
            video_codec="hevc",
            resolution_preset="custom",
            width=160,
            height=90,
        ),
    ],
)
def test_unsupported_codec_or_format_is_explicit(options: RenderOptions, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedRenderConfigurationError):
        FFmpegVideoRenderer(tmp_path)._validate_configuration(render_input(render_options=options))


def test_subprocess_failure_is_truncated_and_wrapped(tmp_path: Path, monkeypatch) -> None:
    error = subprocess.CalledProcessError(1, ["ffmpeg"], stderr="x" * 5000)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    with pytest.raises(FFmpegRenderError) as caught:
        FFmpegVideoRenderer(tmp_path)._run(["ffmpeg", "-version"])

    assert caught.value.__cause__ is error
    assert len(str(caught.value)) < 1300


def test_artifact_validation_requires_file_duration_and_streams(tmp_path: Path) -> None:
    output = tmp_path / "output.mp4"
    with pytest.raises(RenderArtifactValidationError):
        FFmpegVideoRenderer._validate_artifact(output, 1, [])
    output.write_bytes(b"video")
    with pytest.raises(RenderArtifactValidationError):
        FFmpegVideoRenderer._validate_artifact(output, 0, [{"codec_type": "video"}])
    with pytest.raises(RenderArtifactValidationError):
        FFmpegVideoRenderer._validate_artifact(output, 1, [{"codec_type": "video"}])


def test_probe_parses_duration(tmp_path: Path, monkeypatch) -> None:
    renderer = FFmpegVideoRenderer(tmp_path)
    payload = {
        "format": {"duration": "1.25"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    }
    monkeypatch.setattr(
        renderer,
        "_run",
        lambda command: subprocess.CompletedProcess(command, 0, json.dumps(payload), ""),
    )
    duration, streams = renderer._probe(tmp_path / "output.mp4")
    assert duration == 1.25
    assert {item["codec_type"] for item in streams} == {"video", "audio"}


async def test_checksum_metadata_immutability_and_temporary_cleanup(
    tmp_path: Path, monkeypatch
) -> None:
    voice = tmp_path / "voice/8/audio.wav"
    write_wav(voice)
    provider_input = render_input(
        render_options=RenderOptions(
            resolution_preset="custom", width=160, height=90, subtitle_enabled=True
        ),
        timeline=[
            *render_input().model_dump(mode="json")["timeline"],
            {
                "order": 1,
                "item_type": "subtitle",
                "timeline_start_time": 0,
                "timeline_end_time": 0.4,
                "text": "Temporary subtitle",
            },
        ],
    )
    original = provider_input.model_dump(mode="json")
    renderer = FFmpegVideoRenderer(tmp_path)
    subtitle_paths: list[Path] = []

    def fake_run(command: list[str]):
        if command[0] == "ffmpeg":
            subtitle_paths.extend(
                Path(part.split("subtitles='", 1)[1].split("'", 1)[0])
                for part in command
                if "subtitles='" in part
            )
            Path(command[-1]).write_bytes(b"rendered-video")
            return subprocess.CompletedProcess(command, 0, "", "")
        payload = {
            "format": {"duration": "0.4"},
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(renderer, "_run", fake_run)
    result = await renderer.render(provider_input)
    output = tmp_path / "renders/5/output.mp4"

    assert isinstance(result, VideoRenderResult)
    assert result.storage_key == "renders/5/output.mp4"
    assert result.file_size_bytes == output.stat().st_size
    assert result.checksum == f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"
    assert result.duration_seconds == 0.4
    assert result.metadata_data["renderer"] == "ffmpeg"
    assert provider_input.model_dump(mode="json") == original
    assert subtitle_paths
    assert all(not path.exists() for path in subtitle_paths)


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg tooling is not installed",
)
@pytest.mark.parametrize("normalize_audio", [True, False])
async def test_tiny_real_mp4_render(tmp_path: Path, normalize_audio: bool) -> None:
    voice = tmp_path / "voice/8/audio.wav"
    write_wav(voice)

    options = RenderOptions(
        resolution_preset="custom",
        width=160,
        height=90,
        fps=12,
        subtitle_enabled=False,
        normalize_audio=normalize_audio,
    )
    result = await FFmpegVideoRenderer(tmp_path).render(render_input(render_options=options))
    output = tmp_path / result.storage_key

    assert output.is_file()
    assert output.stat().st_size == result.file_size_bytes
    assert result.duration_seconds > 0
    assert result.checksum == f"sha256:{hashlib.sha256(output.read_bytes()).hexdigest()}"
