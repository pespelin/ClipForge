from sqlalchemy import inspect

from app.models.script import Script, ScriptStatus, ScriptTone
from app.models.video import Video
from app.models.video_analysis import VideoAnalysis


def test_script_relationships_are_many_to_one() -> None:
    video_scripts = inspect(Video).relationships.scripts
    analysis_scripts = inspect(VideoAnalysis).relationships.scripts
    script_video = inspect(Script).relationships.video
    script_analysis = inspect(Script).relationships.video_analysis

    assert video_scripts.uselist is True
    assert analysis_scripts.uselist is True
    assert video_scripts.back_populates == "video"
    assert analysis_scripts.back_populates == "video_analysis"
    assert script_video.back_populates == "scripts"
    assert script_analysis.back_populates == "scripts"


def test_video_and_analysis_support_multiple_script_variants() -> None:
    video = Video(id="video-1", filename="video.mp4")
    analysis = VideoAnalysis(id=1, video=video)
    first = Script(
        video=video,
        video_analysis=analysis,
        target_duration_seconds=30,
        tone=ScriptTone.ENGAGING,
        language="en",
    )
    second = Script(
        video=video,
        video_analysis=analysis,
        target_duration_seconds=60,
        tone=ScriptTone.EDUCATIONAL,
        language="en",
    )

    assert video.scripts == [first, second]
    assert analysis.scripts == [first, second]


def test_script_columns_define_defaults_json_and_constraints() -> None:
    table = Script.__table__
    constraint_names = {constraint.name for constraint in table.constraints}

    assert table.c.status.default.arg is ScriptStatus.PENDING
    assert table.c.tone.default.arg is ScriptTone.ENGAGING
    assert table.c.generation_options.nullable is False
    assert table.c.sections.nullable is False
    assert table.c.video_id.unique is not True
    assert table.c.video_analysis_id.unique is not True
    assert {
        "ck_scripts_status",
        "ck_scripts_tone",
        "ck_scripts_target_duration_positive",
        "ck_scripts_estimated_duration_non_negative",
        "ck_scripts_completed_content",
    } <= constraint_names
