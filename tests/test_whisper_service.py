from types import SimpleNamespace

from app.services.whisper_service import WhisperService


def test_whisper_serializes_word_timestamps(tmp_path) -> None:
    service = WhisperService("base")
    service._model = SimpleNamespace(
        transcribe=lambda *args, **kwargs: (
            iter(
                [
                    SimpleNamespace(
                        start=0.0,
                        end=1.0,
                        text=" hello",
                        words=[
                            SimpleNamespace(
                                start=0.0, end=0.4, word=" hello", probability=0.9
                            )
                        ],
                    )
                ]
            ),
            SimpleNamespace(language="en"),
        )
    )

    result = service.transcribe(tmp_path / "audio.wav")

    assert result["transcript"] == "hello"
    assert result["segments"][0]["words"][0]["start"] == 0.0
