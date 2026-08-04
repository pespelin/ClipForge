from celery import Celery

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "youtube_shorts",
    broker=settings.resolved_celery_broker_url,
    backend=settings.resolved_celery_result_backend,
    include=[
        "app.tasks.video",
        "app.tasks.video_analysis",
        "app.tasks.script_generation",
    ],
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)
