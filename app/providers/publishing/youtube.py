import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from app.core.exceptions import (
    PublishingAuthenticationError,
    PublishingError,
    PublishingPermanentError,
    PublishingQuotaExceededError,
    PublishingRateLimitError,
    PublishingTransientError,
)
from app.models.publish_job import PublishPlatform, PublishVisibility
from app.providers.publishing.base import (
    PublishingReconciliationInput,
    PublishingReconciliationResult,
    PublishingRemoteState,
    ResumablePublishingSession,
)
from app.providers.publishing.dependencies import (
    PublishingArtifactReader,
    PublishingCredentialResolver,
)
from app.schemas.publish_job import PublishingInput, PublishingResult


class YouTubePublishingError(Exception):
    """Raised when a YouTube upload cannot complete safely."""

    def __init__(self) -> None:
        super().__init__("YouTube publishing failed")


YouTubeResumableUploadSession = ResumablePublishingSession


@dataclass(frozen=True)
class YouTubeResumableUploadProgress:
    """Outcome of a resumable status probe or media upload."""

    completed: bool
    next_byte_offset: int
    publishing_result: PublishingResult | None = None


class YouTubePublishingProvider:
    """YouTube videos.insert resumable upload adapter."""

    _UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
    _VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"
    _VIDEO_CONTENT_TYPE = "video/mp4"
    _RANGE_PATTERN = re.compile(r"bytes=0-(\d+)\Z")
    _QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg"}
    _RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
    _AUTH_REASONS = {"authError", "invalidCredentials", "insufficientPermissions"}
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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._credential_resolver = credential_resolver
        self._artifact_reader = artifact_reader
        self._http_client = http_client
        self._clock = clock

    async def publish(self, publishing_input: PublishingInput) -> PublishingResult:
        credential, video_bytes = await self._resolve_dependencies(publishing_input)
        session = await self._initiate_session(
            publishing_input,
            video_bytes,
            credential.access_token,
        )
        progress = await self._upload_media(
            session,
            video_bytes,
            credential.access_token,
            publishing_input.visibility,
        )
        if not progress.completed or progress.publishing_result is None:
            raise YouTubePublishingError
        return progress.publishing_result

    async def initiate_upload(
        self, publishing_input: PublishingInput
    ) -> ResumablePublishingSession:
        credential, video_bytes = await self._resolve_dependencies(publishing_input)
        return await self._initiate_session(
            publishing_input,
            video_bytes,
            credential.access_token,
        )

    async def resume_upload(
        self,
        publishing_input: PublishingInput,
        session: ResumablePublishingSession,
    ) -> PublishingResult:
        """Probe and continue an existing session without initiating another one."""
        credential, video_bytes = await self._resolve_dependencies(publishing_input)
        self._validate_session(session, len(video_bytes))
        progress = await self._query_status(
            session,
            credential.access_token,
            publishing_input.visibility,
        )
        if progress.completed:
            if progress.publishing_result is None:
                raise YouTubePublishingError
            return progress.publishing_result
        resumed_session = ResumablePublishingSession(
            session_uri=session.session_uri,
            total_bytes=session.total_bytes,
            next_byte_offset=progress.next_byte_offset,
        )
        progress = await self._upload_media(
            resumed_session,
            video_bytes,
            credential.access_token,
            publishing_input.visibility,
        )
        if not progress.completed or progress.publishing_result is None:
            raise YouTubePublishingError
        return progress.publishing_result

    async def reconcile(
        self,
        reconciliation_input: PublishingReconciliationInput,
    ) -> PublishingReconciliationResult:
        """Query remote state without reading or transferring the local artifact."""
        if reconciliation_input.platform != PublishPlatform.YOUTUBE:
            raise YouTubePublishingError
        credential = await self._resolve_credential(reconciliation_input.account_reference)
        if reconciliation_input.remote_media_id is not None:
            return await self._reconcile_remote_media(
                reconciliation_input.remote_media_id,
                credential.access_token,
                reconciliation_input.visibility,
            )
        if reconciliation_input.resumable_session is not None:
            return await self._reconcile_session(
                reconciliation_input.resumable_session,
                credential.access_token,
                reconciliation_input.visibility,
            )
        return PublishingReconciliationResult(PublishingRemoteState.UNKNOWN)

    async def _resolve_dependencies(self, publishing_input: PublishingInput):
        if publishing_input.platform != PublishPlatform.YOUTUBE:
            raise YouTubePublishingError
        credential = await self._resolve_credential(publishing_input.account_reference)
        try:
            video_bytes = await self._artifact_reader.read(publishing_input.source_storage_key)
        except Exception:
            raise YouTubePublishingError from None
        if not isinstance(video_bytes, bytes) or not video_bytes:
            raise YouTubePublishingError
        return credential, video_bytes

    async def _resolve_credential(self, account_reference: str):
        try:
            credential = await self._credential_resolver.resolve(account_reference)
        except PublishingError:
            raise
        except Exception:
            raise YouTubePublishingError from None
        if credential.token_type is not None and credential.token_type.casefold() != "bearer":
            raise YouTubePublishingError
        if not credential.access_token:
            raise YouTubePublishingError
        return credential

    async def _reconcile_remote_media(
        self,
        remote_media_id: str,
        access_token: str,
        visibility: PublishVisibility,
    ) -> PublishingReconciliationResult:
        query = urlencode({"part": "status,snippet", "id": remote_media_id})
        response = await self._request(
            "GET",
            f"{self._VIDEOS_ENDPOINT}?{query}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if not response.is_success:
            self._raise_classified_error(response)
        try:
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
        except ValueError:
            raise YouTubePublishingError from None
        if not isinstance(items, list):
            raise YouTubePublishingError
        if not items:
            return PublishingReconciliationResult(PublishingRemoteState.NOT_FOUND)
        item = items[0]
        video_id = item.get("id") if isinstance(item, dict) else None
        status = item.get("status") if isinstance(item, dict) else None
        upload_status = status.get("uploadStatus") if isinstance(status, dict) else None
        if not isinstance(video_id, str) or not video_id or video_id != remote_media_id:
            raise YouTubePublishingError
        if upload_status in {"failed", "rejected", "deleted"}:
            return PublishingReconciliationResult(PublishingRemoteState.UNKNOWN)
        if upload_status not in {None, "processed", "uploaded"}:
            return PublishingReconciliationResult(PublishingRemoteState.PROCESSING)
        result = self._remote_result(video_id, visibility, upload_status)
        return PublishingReconciliationResult(PublishingRemoteState.PUBLISHED, result)

    async def _reconcile_session(
        self,
        session: ResumablePublishingSession,
        access_token: str,
        visibility: PublishVisibility,
    ) -> PublishingReconciliationResult:
        response = await self._request(
            "PUT",
            session.session_uri,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Length": "0",
                "Content-Range": f"bytes */{session.total_bytes}",
            },
            content=b"",
        )
        if response.status_code == 308:
            offset = self._next_offset(response.headers.get("Range"), session.total_bytes)
            return PublishingReconciliationResult(
                PublishingRemoteState.INCOMPLETE,
                next_byte_offset=offset,
            )
        if response.status_code == 404:
            return PublishingReconciliationResult(PublishingRemoteState.NOT_FOUND)
        if not response.is_success:
            self._raise_classified_error(response)
        result = self._completion_result(response, visibility)
        return PublishingReconciliationResult(PublishingRemoteState.PUBLISHED, result)

    def _remote_result(
        self,
        video_id: str,
        visibility: PublishVisibility,
        upload_status: str | None,
    ) -> PublishingResult:
        return PublishingResult(
            remote_media_id=video_id,
            remote_url=f"https://www.youtube.com/watch?v={video_id}",
            remote_status=upload_status or "published",
            published_at=self._clock(),
            provider_metadata={
                "provider": "youtube",
                "privacy_status": self._privacy_status(visibility),
                "reconciled": True,
                "video_id": video_id,
            },
        )

    async def _initiate_session(
        self,
        publishing_input: PublishingInput,
        video_bytes: bytes,
        access_token: str,
    ) -> ResumablePublishingSession:
        query = urlencode(
            {
                "part": "snippet,status",
                "uploadType": "resumable",
                "notifySubscribers": str(publishing_input.notify_subscribers).lower(),
            }
        )
        metadata = json.dumps(
            self._metadata(publishing_input),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        response = await self._request(
            "POST",
            f"{self._UPLOAD_ENDPOINT}?{query}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": self._VIDEO_CONTENT_TYPE,
                "X-Upload-Content-Length": str(len(video_bytes)),
            },
            content=metadata,
        )
        if not response.is_success:
            self._raise_classified_error(response)
        session_uri = response.headers.get("Location")
        if not session_uri or not session_uri.strip():
            raise YouTubePublishingError
        return ResumablePublishingSession(
            session_uri=session_uri,
            total_bytes=len(video_bytes),
        )

    async def _query_status(
        self,
        session: ResumablePublishingSession,
        access_token: str,
        visibility: PublishVisibility,
    ) -> YouTubeResumableUploadProgress:
        response = await self._request(
            "PUT",
            session.session_uri,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Length": "0",
                "Content-Range": f"bytes */{session.total_bytes}",
            },
            content=b"",
        )
        return self._interpret_response(response, session.total_bytes, visibility)

    async def _upload_media(
        self,
        session: ResumablePublishingSession,
        video_bytes: bytes,
        access_token: str,
        visibility: PublishVisibility,
    ) -> YouTubeResumableUploadProgress:
        self._validate_session(session, len(video_bytes))
        start = session.next_byte_offset
        end = session.total_bytes - 1
        body = video_bytes[start:]
        response = await self._request(
            "PUT",
            session.session_uri,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": self._VIDEO_CONTENT_TYPE,
                "Content-Length": str(len(body)),
                "Content-Range": f"bytes {start}-{end}/{session.total_bytes}",
            },
            content=body,
        )
        return self._interpret_response(response, session.total_bytes, visibility)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            return await self._http_client.request(method, url, timeout=30.0, **kwargs)
        except (httpx.TimeoutException, httpx.RequestError):
            raise PublishingTransientError from None

    def _interpret_response(
        self,
        response: httpx.Response,
        total_bytes: int,
        visibility: PublishVisibility,
    ) -> YouTubeResumableUploadProgress:
        if response.status_code == 308:
            offset = self._next_offset(response.headers.get("Range"), total_bytes)
            return YouTubeResumableUploadProgress(False, offset)
        if not response.is_success:
            self._raise_classified_error(response)
        result = self._completion_result(response, visibility)
        return YouTubeResumableUploadProgress(True, total_bytes, result)

    @classmethod
    def _raise_classified_error(cls, response: httpx.Response) -> None:
        status = response.status_code
        reasons = cls._error_reasons(response)
        retry_after = cls._retry_after_seconds(response.headers.get("Retry-After"))
        if reasons & cls._QUOTA_REASONS:
            raise PublishingQuotaExceededError
        if status == 429 or reasons & cls._RATE_LIMIT_REASONS:
            raise PublishingRateLimitError(retry_after_seconds=retry_after)
        if status == 401 or reasons & cls._AUTH_REASONS:
            raise PublishingAuthenticationError
        if status in {408, 500, 502, 503, 504}:
            raise PublishingTransientError(retry_after_seconds=retry_after)
        if 400 <= status < 500:
            raise PublishingPermanentError
        raise YouTubePublishingError

    @staticmethod
    def _error_reasons(response: httpx.Response) -> set[str]:
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else None
            errors = error.get("errors") if isinstance(error, dict) else None
            if not isinstance(errors, list):
                return set()
            return {
                reason
                for item in errors
                if isinstance(item, dict) and isinstance((reason := item.get("reason")), str)
            }
        except (TypeError, ValueError):
            return set()

    @staticmethod
    def _retry_after_seconds(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            seconds = int(value)
        except ValueError:
            return None
        return seconds if seconds >= 0 else None

    @classmethod
    def _next_offset(cls, range_header: str | None, total_bytes: int) -> int:
        if range_header is None:
            return 0
        match = cls._RANGE_PATTERN.fullmatch(range_header)
        if match is None:
            raise YouTubePublishingError
        last_byte = int(match.group(1))
        if last_byte < 0 or last_byte >= total_bytes:
            raise YouTubePublishingError
        return last_byte + 1

    @staticmethod
    def _validate_session(
        session: ResumablePublishingSession,
        artifact_size: int,
    ) -> None:
        if (
            not session.session_uri
            or session.total_bytes != artifact_size
            or session.total_bytes <= 0
            or session.next_byte_offset < 0
            or session.next_byte_offset >= session.total_bytes
        ):
            raise YouTubePublishingError

    def _completion_result(
        self,
        response: httpx.Response,
        visibility: PublishVisibility,
    ) -> PublishingResult:
        try:
            payload = response.json()
            video_id = payload.get("id") if isinstance(payload, dict) else None
        except ValueError:
            raise YouTubePublishingError from None
        if not isinstance(video_id, str) or not video_id:
            raise YouTubePublishingError
        privacy_status = self._privacy_status(visibility)
        return PublishingResult(
            remote_media_id=video_id,
            remote_url=f"https://www.youtube.com/watch?v={video_id}",
            remote_status="published",
            published_at=self._clock(),
            provider_metadata={
                "provider": "youtube",
                "privacy_status": privacy_status,
                "upload_type": "resumable",
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
