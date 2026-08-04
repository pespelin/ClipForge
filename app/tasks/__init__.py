"""Background task definitions."""

from app.tasks.video import process_video
from app.tasks.video_analysis import analyze_video

__all__ = ["analyze_video", "process_video"]
