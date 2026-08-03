from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository[ModelT]:
    """Base class for persistence adapters; concrete queries belong in subclasses."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
