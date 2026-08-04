# YouTube Shorts Automation Platform

Production-oriented backend for a future AI-powered YouTube Shorts SaaS. Phase 3 implements
video ingestion, transcription, and structured offline video analysis. Script generation and later
content-production phases are not yet implemented.

## Architecture

The project applies Clean Architecture boundaries:

- `app/api/` — FastAPI transport, versioned routers, request dependencies, and HTTP-only concerns.
- `app/core/` — configuration, structured logging, Redis lifecycle, and global exception handling.
- `app/services/` — application use cases and orchestration.
- `app/repositories/` — persistence ports/implementations, isolated from HTTP transport.
- `app/models/` — SQLAlchemy ORM persistence models, registered for Alembic discovery.
- `app/schemas/` — Pydantic v2 boundary contracts.
- `app/providers/` — provider ports and replaceable adapters for LLM, TTS, and media APIs.
- `app/tasks/` and `app/workers/` — Celery task definitions and worker composition.
- `app/db/` — SQLAlchemy metadata, async engine, and session dependency.
- `app/utils/` — framework-neutral shared utilities.

Provider interfaces decouple application services from vendors. Video analysis currently uses
`LocalVideoAnalyzer`, a deterministic offline adapter intended for development and testing. It
requires no API key or network access. Its frequency-based topics, fixed transcript windows, and
small rule-based English sentiment vocabulary are not production-grade semantic analysis.

## Local setup

1. Copy the environment template: `cp .env.example .env`.
2. Set real secrets only in `.env` (never commit it).
3. Install dependencies: `uv sync --all-groups`.
4. Start infrastructure and the API: `docker compose up --build`.
5. Visit `http://localhost:8000/docs` or call `GET /api/v1/health`.

Host-side development connects to Docker PostgreSQL at `localhost:5433`. Containers connect over
the Compose network at `postgres:5432`. Apply migrations inside the API container after starting
the stack:

```bash
docker compose exec api uv run --no-sync alembic upgrade head
```

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

Both API and worker containers mount the application directory, so they share this storage during
local Compose development.

## Video analysis (Phase 3)

After Phase 2 commits the transcript and marks the video completed, the `videos.process` Celery task
enqueues the separate `videos.analyze` task. That task composes `VideoRepository`,
`VideoAnalysisRepository`, `VideoAnalysisService`, and `LocalVideoAnalyzer` using the existing async
database session. Analysis never runs inline in the upload or analysis HTTP request.

The analysis state machine is:

```text
pending → processing → completed
                     ↘ failed
```

The persisted result includes a summary, topics, keywords, sentiment, hook candidates, and
Shorts-compatible clip candidates. Analyzer and validation failures persist the failed state and a
useful error message before the Celery task is reported as failed.

### Analysis API

- `POST /api/v1/videos/{video_id}/analysis` creates or reuses an analysis request. New, pending, and
  failed work is queued and returns `202 Accepted`. Processing or completed analysis is returned
  with `200 OK` without duplicate enqueueing.
- `GET /api/v1/videos/{video_id}/analysis` returns a status response for pending, processing, or
  failed work and the complete structured result after completion.

Completed analysis is idempotent at both the API and service/task boundaries. Repeated task runs do
not call the analyzer again, and the database enforces one analysis row per video.

### Current limitations

- `LocalVideoAnalyzer` uses deterministic heuristics rather than audiovisual or semantic models.
- Transcript word timestamps are not part of the analyzer input contract, so hooks and clips use
  conservative deterministic windows bounded by known video duration.
- Database commit and Celery publication are separate operations. Broker failures are persisted,
  and pending work can be safely re-enqueued, but a transactional outbox is not implemented.
- Only the local analyzer is wired; external provider selection is intentionally deferred.

## Development commands

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose up -d --build
docker compose exec api uv run --no-sync alembic upgrade head
docker compose ps
curl --fail http://localhost:8000/api/v1/health
```

For host-side migration development, `.env` must use `POSTGRES_HOST=localhost` and
`POSTGRES_PORT=5433`; then `uv run alembic upgrade head` targets Docker PostgreSQL through its
published port. Inside Compose, `POSTGRES_HOST=postgres` and `POSTGRES_PORT=5432` are supplied to
the API and worker containers. Alembic obtains the database URL from the same validated settings
model as the application. Models must be imported through `app.models` for autogeneration.

## Architecture conventions

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
