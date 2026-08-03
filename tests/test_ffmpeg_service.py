import json
import subprocess

from app.services.ffmpeg_service import FFmpegService


def test_extract_metadata_parses_ffprobe_output(tmp_path, monkeypatch) -> None:
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    payload = {
        "format": {"duration": "5.5", "bit_rate": "1000"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""),
    )

    metadata = FFmpegService().extract_metadata(video)

    assert metadata.codec == "h264"
    assert metadata.audio_codec == "aac"
    assert metadata.duration == 5.5
