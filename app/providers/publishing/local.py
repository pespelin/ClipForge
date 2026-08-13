import hashlib
import json
from datetime import UTC, datetime, timedelta

from app.models.publish_job import PublishPlatform
from app.schemas.publish_job import PublishingInput, PublishingResult


class UnsupportedPublishingPlatformError(ValueError):
    """Raised when the local simulator receives a non-YouTube platform."""


class UnusablePublishingInputError(ValueError):
    """Raised when deterministic publishing metadata cannot be simulated safely."""


class LocalPublishingProvider:
    """Deterministic metadata-only publishing simulator for development.

    No source artifact is opened or uploaded. Synthetic timestamps are derived
    from stable input unless an already-due scheduled time is supplied.
    """

    _SYNTHETIC_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)
    _SYNTHETIC_RANGE_SECONDS = 20 * 365 * 24 * 60 * 60

    async def publish(self, publishing_input: PublishingInput) -> PublishingResult:
        self._validate_input(publishing_input)
        identity_hash = self._identity_hash(publishing_input)
        remote_media_id = f"local-youtube-{identity_hash[:24]}"
        published_at = self._published_at(publishing_input, identity_hash)

        return PublishingResult(
            remote_media_id=remote_media_id,
            remote_url=(f"https://publishing.local.invalid/youtube/{remote_media_id}"),
            remote_status="published",
            published_at=published_at,
            provider_metadata={
                "synthetic": True,
                "provider": "local",
                "simulated_platform": "youtube",
                "account_reference": publishing_input.account_reference,
                "source_checksum": publishing_input.source_checksum,
                "visibility": publishing_input.visibility.value,
                "scheduled": publishing_input.scheduled_publish_at is not None,
                "made_for_kids": publishing_input.made_for_kids,
                "notify_subscribers": publishing_input.notify_subscribers,
                "language": publishing_input.language,
                "tags": list(publishing_input.tags),
                "identity_hash": identity_hash,
                "real_publication": False,
            },
        )

    @staticmethod
    def _validate_input(publishing_input: PublishingInput) -> None:
        if publishing_input.platform != PublishPlatform.YOUTUBE:
            value = getattr(publishing_input.platform, "value", publishing_input.platform)
            raise UnsupportedPublishingPlatformError(
                f"LocalPublishingProvider cannot simulate platform {value}"
            )
        if not publishing_input.account_reference.strip():
            raise UnusablePublishingInputError("account_reference must not be blank")
        if not publishing_input.source_storage_key.strip():
            raise UnusablePublishingInputError("source_storage_key must not be blank")
        if (
            publishing_input.source_checksum is not None
            and not publishing_input.source_checksum.strip()
        ):
            raise UnusablePublishingInputError("source_checksum must not be blank")
        if publishing_input.source_file_size_bytes < 0:
            raise UnusablePublishingInputError("source_file_size_bytes must not be negative")
        if publishing_input.source_duration_seconds <= 0:
            raise UnusablePublishingInputError("source_duration_seconds must be positive")
        if not publishing_input.title.strip():
            raise UnusablePublishingInputError("title must not be blank")

        scheduled = publishing_input.scheduled_publish_at
        if scheduled is None:
            return
        if scheduled.tzinfo is None or scheduled.utcoffset() is None:
            raise UnusablePublishingInputError(
                "scheduled_publish_at must include timezone information"
            )
        if scheduled > datetime.now(UTC):
            raise UnusablePublishingInputError("scheduled publication time is still in the future")

    @classmethod
    def _identity_hash(cls, publishing_input: PublishingInput) -> str:
        scheduled = publishing_input.scheduled_publish_at
        payload = {
            "publish_job_id": publishing_input.publish_job_id,
            "video_render_id": publishing_input.video_render_id,
            "platform": publishing_input.platform.value,
            "account_reference": publishing_input.account_reference,
            "source_storage_key": publishing_input.source_storage_key,
            "source_checksum": publishing_input.source_checksum,
            "source_file_size_bytes": publishing_input.source_file_size_bytes,
            "source_duration_seconds": publishing_input.source_duration_seconds,
            "title": publishing_input.title,
            "visibility": publishing_input.visibility.value,
            "scheduled_publish_at": (
                scheduled.astimezone(UTC).isoformat() if scheduled is not None else None
            ),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _published_at(cls, publishing_input: PublishingInput, identity_hash: str) -> datetime:
        if publishing_input.scheduled_publish_at is not None:
            return publishing_input.scheduled_publish_at.astimezone(UTC)
        offset = int(identity_hash[:16], 16) % cls._SYNTHETIC_RANGE_SECONDS
        return cls._SYNTHETIC_EPOCH + timedelta(seconds=offset)
