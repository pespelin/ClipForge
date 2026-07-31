from typing import Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Base class for persistence adapters; concrete queries belong in subclasses."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
