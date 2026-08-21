import asyncio
from pathlib import Path


class PublishingArtifactReadError(Exception):
    """Raised when a persisted artifact cannot be read safely."""

    def __init__(self) -> None:
        super().__init__("Publishing artifact could not be read")


class FilesystemPublishingArtifactReader:
    """Read storage keys rooted under the configured local storage directory."""

    def __init__(self, storage_root: str | Path) -> None:
        self._storage_root = Path(storage_root).resolve()

    async def read(self, storage_key: str) -> bytes:
        try:
            path = (self._storage_root / storage_key).resolve()
            path.relative_to(self._storage_root)
            if not path.is_file():
                raise PublishingArtifactReadError
            return await asyncio.to_thread(path.read_bytes)
        except PublishingArtifactReadError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise PublishingArtifactReadError from None
