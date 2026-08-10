from typing import Protocol

from app.schemas.video_render import VideoRenderInput, VideoRenderResult


class VideoRenderer(Protocol):
    """Provider-neutral boundary for producing a rendered video artifact."""

    async def render(self, render_input: VideoRenderInput) -> VideoRenderResult:
        """Render from validated data without database access or ORM entities."""
        ...
