from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.script import ScriptStatus, ScriptTone
from app.schemas.script import (
    ScriptGenerationOptions,
    ScriptGenerationRequest,
    ScriptResponse,
    ScriptSection,
)


def options(**values) -> ScriptGenerationOptions:
    defaults = {
        "target_duration_seconds": 45,
        "tone": ScriptTone.ENGAGING,
        "language": "en",
        "include_call_to_action": True,
    }
    defaults.update(values)
    return ScriptGenerationOptions.model_validate(defaults)


def completed_response(**values) -> ScriptResponse:
    timestamp = datetime.now(UTC)
    defaults = {
        "id": 1,
        "video_id": "video-1",
        "video_analysis_id": 1,
        "status": ScriptStatus.COMPLETED,
        "title": "Three editing lessons",
        "hook": "Most edits lose viewers in three seconds.",
        "body": "Start with the strongest visual and remove every pause.",
        "call_to_action": "Follow for more editing tips.",
        "full_script": "Most edits lose viewers. Start strong and remove every pause.",
        "estimated_duration_seconds": 35,
        "target_duration_seconds": 45,
        "tone": ScriptTone.ENGAGING,
        "language": "en",
        "generation_options": options(),
        "sections": [
            ScriptSection(
                order=0,
                type="hook",
                text="Most edits lose viewers.",
                estimated_duration_seconds=3,
                source_start_time=0,
                source_end_time=3,
            )
        ],
        "created_at": timestamp,
        "updated_at": timestamp,
        "completed_at": timestamp,
    }
    defaults.update(values)
    return ScriptResponse.model_validate(defaults)


def test_generation_options_and_request_validate() -> None:
    generation_options = options(
        preferred_hook_candidate_index=0,
        preferred_clip_candidate_index=2,
        tone=ScriptTone.EDUCATIONAL,
        language="en-US",
    )
    request = ScriptGenerationRequest(video_analysis_id=3, options=generation_options)

    assert request.options.target_duration_seconds == 45
    assert request.options.preferred_clip_candidate_index == 2
    assert request.options.tone is ScriptTone.EDUCATIONAL


@pytest.mark.parametrize(
    "values",
    [
        {"target_duration_seconds": 0},
        {"target_duration_seconds": -1},
        {"preferred_hook_candidate_index": -1},
        {"preferred_clip_candidate_index": -1},
        {"language": "english"},
        {"language": "EN-us"},
    ],
)
def test_generation_options_reject_invalid_values(values: dict) -> None:
    with pytest.raises(ValidationError):
        options(**values)


def test_section_validates_durations_and_source_range() -> None:
    section = ScriptSection(
        order=1,
        type="body",
        text="Explain the main point.",
        estimated_duration_seconds=10,
        source_start_time=5,
        source_end_time=15,
    )

    assert section.source_end_time == 15


@pytest.mark.parametrize(
    "values",
    [
        {"estimated_duration_seconds": -1},
        {"source_start_time": -1},
        {"source_end_time": -1},
        {"source_start_time": 5, "source_end_time": 5},
        {"source_start_time": 5, "source_end_time": 4},
    ],
)
def test_section_rejects_invalid_durations_and_source_range(values: dict) -> None:
    with pytest.raises(ValidationError):
        ScriptSection(order=0, type="body", text="Text", **values)


@pytest.mark.parametrize("field", ["title", "hook", "body", "full_script"])
def test_completed_script_rejects_missing_or_whitespace_content(field: str) -> None:
    with pytest.raises(ValidationError, match="completed script requires"):
        completed_response(**{field: "   "})


def test_non_completed_script_may_have_empty_content() -> None:
    response = completed_response(
        status=ScriptStatus.PENDING,
        title=None,
        hook=None,
        body=None,
        full_script=None,
        completed_at=None,
    )

    assert response.status is ScriptStatus.PENDING


def test_completed_script_response_accepts_sections_and_options() -> None:
    response = completed_response()

    assert response.status is ScriptStatus.COMPLETED
    assert response.sections[0].type == "hook"
    assert response.generation_options.include_call_to_action is True
