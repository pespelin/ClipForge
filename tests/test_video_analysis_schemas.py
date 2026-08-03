from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.video_analysis import AnalysisStatus
from app.schemas.video_analysis import (
    ClipCandidate,
    HookCandidate,
    TopicResult,
    VideoAnalysisResponse,
)


def test_complete_analysis_response_validates_structured_results() -> None:
    timestamp = datetime.now(UTC)

    response = VideoAnalysisResponse(
        id=1,
        video_id="video-1",
        status=AnalysisStatus.COMPLETED,
        summary="A concise summary.",
        topics=[TopicResult(name="Editing", relevance=0.9)],
        keywords=["video", "editing"],
        sentiment="positive",
        hook_candidates=[HookCandidate(text="Start here", start_time=1.0, end_time=4.0, score=0.8)],
        clip_candidates=[
            ClipCandidate(title="Main insight", start_time=5.0, end_time=25.0, score=0.95)
        ],
        created_at=timestamp,
        updated_at=timestamp,
        completed_at=timestamp,
    )

    assert response.status is AnalysisStatus.COMPLETED
    assert response.topics[0].name == "Editing"
    assert response.clip_candidates[0].end_time == 25.0


@pytest.mark.parametrize("candidate_type", [HookCandidate, ClipCandidate])
def test_candidate_rejects_invalid_time_range(candidate_type: type) -> None:
    values = {"start_time": 10.0, "end_time": 5.0}
    if candidate_type is HookCandidate:
        values["text"] = "Invalid hook"
    else:
        values["title"] = "Invalid clip"

    with pytest.raises(ValidationError, match="end_time must be greater"):
        candidate_type.model_validate(values)


def test_topic_relevance_must_be_normalized() -> None:
    with pytest.raises(ValidationError):
        TopicResult(name="Editing", relevance=1.1)
