"""SQLAlchemy ORM models; import models here so Alembic discovers them."""

from app.models.video import Transcript, Video

__all__ = ["Transcript", "Video"]
