import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing.dependencies import (
    PublishingArtifactReader,
    PublishingCredentialResolver,
)
from app.schemas.publish_job import PublishingInput, PublishingResult


class YouTubePublishingError(Exception):
    """Raised when a YouTube upload cannot complete safely."""

    def __init__(self) -> None:
        super().__init__("YouTube publishing failed")


class YouTubePublishingProvider:
    """YouTube videos.insert multipart upload adapter."""

    _UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
    _VIDEO_CONTENT_TYPE = "video/mp4"
    _PRIVACY_STATUS = {
        PublishVisibility.PUBLIC: "public",
        PublishVisibility.PRIVATE: "private",
        PublishVisibility.UNLISTED: "unlisted",
    }

    def __init__(
        self,
        credential_resolver: PublishingCredentialResolver,
        artifact_reader: PublishingArtifactReader,
        http_client: httpx.AsyncClient,
        *,
        boundary_factory: Callable[[], str] = lambda: secrets.token_hex(16),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._credential_resolver = credential_resolver
        self._artifact_reader = artifact_reader
        self._http_client = http_client
        self._boundary_factory = boundary_factory
        self._clock = clock

    async def publish(self, publishing_input: PublishingInput) -> PublishingResult:
        if publishing_input.platform != PublishPlatform.YOUTUBE:
            raise YouTubePublishingError

        try:
            credential = await self._credential_resolver.resolve(publishing_input.account_reference)
        except Exception:
            raise YouTubePublishingError from None
        if credential.token_type is not None and credential.token_type.casefold() != "bearer":
            raise YouTubePublishingError
        if not credential.access_token:
            raise YouTubePublishingError

        try:
            video_bytes = await self._artifact_reader.read(publishing_input.source_storage_key)
        except Exception:
            raise YouTubePublishingError from None
        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise YouTubePublishingError

        boundary = self._boundary_factory()
        if not boundary or any(character in boundary for character in '\r\n"'):
            raise YouTubePublishingError
        body = self._multipart_body(
            boundary,
            self._metadata(publishing_input),
            video_bytes,
        )
        query = urlencode(
            {
                "part": "snippet,status",
                "uploadType": "multipart",
                "notifySubscribers": str(publishing_input.notify_subscribers).lower(),
            }
        )
        try:
            response = await self._http_client.post(
                f"{self._UPLOAD_ENDPOINT}?{query}",
                headers={
                    "Authorization": f"Bearer {credential.access_token}",
                    "Content-Type": f'multipart/related; boundary="{boundary}"',
                },
                content=body,
                timeout=30.0,
            )
        except (httpx.TimeoutException, httpx.RequestError):
            raise YouTubePublishingError from None
        if not response.is_success:
            raise YouTubePublishingError

        try:
            payload = response.json()
            video_id = payload.get("id") if isinstance(payload, dict) else None
        except ValueError:
            raise YouTubePublishingError from None
        if not isinstance(video_id, str) or not video_id:
            raise YouTubePublishingError

        privacy_status = self._privacy_status(publishing_input.visibility)
        return PublishingResult(
            remote_media_id=video_id,
            remote_url=f"https://www.youtube.com/watch?v={video_id}",
            remote_status="published",
            published_at=self._clock(),
            provider_metadata={
                "provider": "youtube",
                "privacy_status": privacy_status,
                "upload_type": "multipart",
                "video_id": video_id,
            },
        )

    @classmethod
    def _metadata(cls, publishing_input: PublishingInput) -> dict:
        snippet: dict = {
            "title": publishing_input.title,
            "tags": list(publishing_input.tags),
            "defaultLanguage": publishing_input.language,
        }
        if publishing_input.description is not None:
            snippet["description"] = publishing_input.description
        return {
            "snippet": snippet,
            "status": {
                "privacyStatus": cls._privacy_status(publishing_input.visibility),
                "selfDeclaredMadeForKids": publishing_input.made_for_kids,
            },
        }

    @classmethod
    def _privacy_status(cls, visibility: PublishVisibility) -> str:
        try:
            return cls._PRIVACY_STATUS[visibility]
        except KeyError:
            raise YouTubePublishingError from None

    @classmethod
    def _multipart_body(cls, boundary: str, metadata: dict, video_bytes: bytes) -> bytes:
        metadata_bytes = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return b"".join(
            (
                f"--{boundary}\r\n".encode(),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                metadata_bytes,
                f"\r\n--{boundary}\r\n".encode(),
                f"Content-Type: {cls._VIDEO_CONTENT_TYPE}\r\n\r\n".encode(),
                video_bytes,
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
