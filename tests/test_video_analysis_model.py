from sqlalchemy import inspect

from app.models.video import Video
from app.models.video_analysis import AnalysisStatus, VideoAnalysis


def test_video_analysis_relationship_is_one_to_one() -> None:
    video_relationship = inspect(Video).relationships.analysis
    analysis_relationship = inspect(VideoAnalysis).relationships.video

    assert video_relationship.uselist is False
    assert video_relationship.back_populates == "video"
    assert analysis_relationship.back_populates == "analysis"
    assert VideoAnalysis.__table__.c.video_id.unique is True


def test_video_analysis_columns_define_status_and_json_constraints() -> None:
    table = VideoAnalysis.__table__

    assert table.c.status.default.arg is AnalysisStatus.PENDING
    assert table.c.topics.nullable is False
    assert table.c.keywords.nullable is False
    assert table.c.hook_candidates.nullable is False
    assert table.c.clip_candidates.nullable is False
    assert AnalysisStatus.PENDING.value == "pending"
