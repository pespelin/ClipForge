from app.workers.celery_app import celery_app


@celery_app.task(name="shorts.placeholder")
def shorts_placeholder() -> None:
    """Task registration smoke-test; replace when the workflow is specified."""
