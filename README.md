# YouTube Shorts Automation Platform

Production-oriented backend for a future AI-powered YouTube Shorts SaaS. Phase 2 implements video ingestion and transcription only; no content-generation workflow is included.

## Architecture

The project applies Clean Architecture boundaries:

- `app/api/` — FastAPI transport, versioned routers, request dependencies, and HTTP-only concerns.
- `app/core/` — configuration, structured logging, Redis lifecycle, and global exception handling.
- `app/services/` — future application use cases and orchestration.
- `app/repositories/` — persistence ports/implementations, isolated from HTTP transport.
- `app/models/` — SQLAlchemy ORM persistence models, registered for Alembic discovery.
- `app/schemas/` — Pydantic v2 boundary contracts.
- `app/providers/` — provider ports and replaceable adapters for LLM, TTS, and media APIs.
- `app/tasks/` and `app/workers/` — Celery task definitions and worker composition.
- `app/db/` — SQLAlchemy metadata, async engine, and session dependency.
- `app/utils/` — framework-neutral shared utilities.

Provider interfaces decouple application services from vendors. `LLMProvider` has OpenAI-compatible, Gemini, and future Claude adapter placeholders; `TTSProvider` has a Kokoro placeholder. Pexels is similarly isolated as a media provider.

## Local setup

1. Copy the environment template: `cp .env.example .env`.
2. Set real secrets only in `.env` (never commit it).
3. Install dependencies: `uv sync --all-groups`.
4. Start infrastructure and the API: `docker compose up --build`.
5. Visit `http://localhost:8000/docs` or call `GET /api/v1/health`.

The Compose stack runs `api`, `worker`, PostgreSQL, and Redis. The API and database code use async SQLAlchemy with `asyncpg`; Celery uses Redis as broker and result backend. The image includes FFmpeg. `faster-whisper` runs in the Celery worker; its model is downloaded by the worker on first use.

## Video ingestion and transcription (Phase 2)

`POST /api/v1/videos/upload` accepts a multipart `file` in MP4, MOV, MKV, or WEBM format. The API streams the file to storage, creates a `Video` row, and immediately queues a Celery job, returning `202 Accepted`:

```json
{"video_id":"uuid", "status":"uploaded"}
```

The worker advances the video through `uploaded` → `processing` → `completed` (or `failed`). It runs `ffprobe` to read metadata, uses FFmpeg to generate mono 16 kHz PCM WAV audio, transcribes it with faster-whisper (including word timestamps), stores `Transcript`, and writes the JSON transcript artifact.

`GET /api/v1/videos/{video_id}` returns the processing status, available metadata, duration, language, and `transcript_ready` flag. A missing video returns `404`; unsupported formats return `415`; files exceeding `MAX_UPLOAD_SIZE_MB` return `413`.

### Storage layout

The local storage adapter is configured with `STORAGE_ROOT` (default `storage/videos`) rather than route-level hardcoded paths. Its key layout is designed for an S3-compatible replacement:

```
storage/videos/{video_id}/original.{mp4|mov|mkv|webm}
storage/videos/{video_id}/audio.wav
storage/videos/{video_id}/transcript.json
```

Both API and worker containers mount the application directory, so they share this storage during local Compose development. Run `uv run alembic upgrade head` before using the ingestion endpoints.

## Development commands

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

Alembic obtains the database URL from the same validated settings model as the application. Models must be imported through `app.models` for autogeneration.

## Conventions for the next phase

- Add business workflows in `services`, never in route handlers or Celery tasks.
- Keep vendor SDK calls inside provider adapters; inject provider interfaces into services.
- Keep database queries inside repositories and return explicit schemas at API boundaries.
- Add migrations for every persisted-model change; do not create tables during API startup.
- Put CPU- or long-running work behind Celery tasks; task functions should delegate to services.

Future Roadmap

Phase 1
Backend Infrastructure

Phase 2
Video Ingestion

Phase 3
Video Analysis

Phase 4
Script Generation

Phase 5
Voice Generation

Phase 6
B-roll Retrieval

Phase 7
Video Rendering

Phase 8
Publishing
