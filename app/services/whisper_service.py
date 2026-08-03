from pathlib import Path
from typing import Any

from app.core.exceptions import MediaProcessingError


class WhisperService:
    def __init__(self, model_name: str, device: str = "auto", compute_type: str = "int8") -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model: Any = None

    @property
    def model(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=self.compute_type
                )
            except Exception as exc:
                raise MediaProcessingError from exc
        return self._model

    def transcribe(self, audio_path: Path) -> dict[str, Any]:
        try:
            segments, info = self.model.transcribe(str(audio_path), word_timestamps=True)
            serialized_segments = []
            for segment in segments:
                words = [
                    {
                        "start": word.start,
                        "end": word.end,
                        "word": word.word,
                        "probability": word.probability,
                    }
                    for word in (segment.words or [])
                ]
                serialized_segments.append(
                    {
                        "start": segment.start,
                        "end": segment.end,
                        "text": segment.text,
                        "words": words,
                    }
                )
            return {
                "transcript": " ".join(
                    item["text"].strip() for item in serialized_segments
                ).strip(),
                "language": info.language,
                "segments": serialized_segments,
                "timestamps": [
                    {"start": item["start"], "end": item["end"]} for item in serialized_segments
                ],
            }
        except MediaProcessingError:
            raise
        except Exception as exc:
            raise MediaProcessingError from exc
