from typing import Protocol

from app.schemas.broll import MediaCandidateResult, MediaSearchInput


class MediaProvider(Protocol):
    """Provider-neutral boundary for B-roll candidate searches."""

    async def search(self, search_input: MediaSearchInput) -> list[MediaCandidateResult]:
        """Return candidates without persistence, downloads, or vendor SDK types."""
        ...
