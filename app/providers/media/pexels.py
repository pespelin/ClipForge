class PexelsProvider:
    """Pexels media-search adapter placeholder."""

    async def search_videos(self, query: str) -> list[dict[str, object]]:
        raise NotImplementedError("Pexels provider integration is not implemented yet")
