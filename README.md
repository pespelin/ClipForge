# YouTube Shorts Automation Platform

Production-oriented backend for a future AI-powered YouTube Shorts SaaS. Phases 1–8 implement
backend infrastructure, video ingestion and transcription, structured video analysis, and
deterministic offline Shorts script, voice generation, B-roll retrieval, and local video
rendering and publishing simulation.

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
Script generation similarly uses `LocalScriptGenerator`, an English-only deterministic adapter
with fixed tone templates and word-budget duration estimates. It is a development fallback, not a
production copywriting or translation system.
Voice generation uses `LocalTTSProvider`, which creates deterministic synthetic PCM WAV tones using
only the Python standard library. It provides real artifacts for workflow development but does not
produce human-like or intelligible speech.
The current `LocalMediaProvider` produces deterministic synthetic B-roll metadata with deliberately
non-resolving `local.invalid` URLs. It supports offline workflow development but does not search,
license, download, or store real media.
Video rendering uses `FFmpegVideoRenderer`, a local adapter that consumes provider-neutral render
contracts and produces validated MP4/H.264/AAC artifacts without accessing application persistence.
Publishing uses `LocalPublishingProvider`, a deterministic metadata-only simulator. It creates
synthetic `local-youtube-*` identities and `publishing.local.invalid` URLs without network access,
credentials, OAuth, or a real upload.

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
storage/videos/voice/{voice_track_id}/audio.wav
storage/videos/renders/{render_id}/output.mp4
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

## Script generation (Phase 4)

A completed `VideoAnalysis` provides the structured input for script generation. A `Video` has one
analysis and may have multiple `Script` rows; every script references both its source video and the
specific analysis used to generate it. This supports multiple explicit variants with independent
duration, tone, language, CTA, and preferred-candidate options without option-based deduplication.

The HTTP request creates and commits a pending row before publishing the separate
`scripts.generate` Celery task. The task opens the existing async database session and composes
`VideoRepository`, `VideoAnalysisRepository`, `ScriptRepository`, `ScriptGenerationService`, and
`LocalScriptGenerator`. Generation runs outside the HTTP request and persists structured hook,
body, CTA, full-script, duration, and section results.

The script state machine is:

```text
pending → generating → completed
          ↖          ↘ failed
          └── retry ──┘
```

Generator failures persist `failed`, clear `completed_at`, and retain a useful error message before
the Celery task reports failure. Retrying a pending or failed script reuses the same database row;
generating and completed scripts are not enqueued again.

### Script API

- `POST /api/v1/videos/{video_id}/scripts` creates a new pending variant, commits it, queues
  `scripts.generate`, and returns `202 Accepted`.
- `GET /api/v1/videos/{video_id}/scripts` lists variants newest first, returning compact status
  objects for unfinished rows and full structured results for completed rows.
- `GET /api/v1/scripts/{script_id}` returns the current status or complete generated script.
- `POST /api/v1/scripts/{script_id}/retry` re-enqueues the same pending or failed row with
  `202 Accepted`. Generating or completed rows return `200 OK` without duplicate publication.

Each explicit create request is a new variant, even when its options match an existing variant.
Conversely, task reruns and completed-script retries are idempotent: they do not call the generator
again or create additional rows.

### Script-generation limitations

- `LocalScriptGenerator` supports English output only and raises an explicit error for unsupported
  languages; it does not translate.
- Tone adaptation uses transparent deterministic templates rather than semantic generation.
- Spoken duration uses a fixed 150-words-per-minute estimate and is not frame-accurate.
- Source timestamps come from selected analysis candidates when available and otherwise use safe,
  duration-bounded fallbacks.
- No external script provider or runtime provider-selection configuration is wired yet.
- Database commits and Celery broker publication are not atomic. Synchronous publication failures
  are persisted on the same script row, and retry is safe, but a process crash between commit and
  publication can leave pending work unqueued. A transactional outbox is not implemented.

## Voice generation (Phase 5)

A completed `Script` may own multiple `VoiceTrack` rows. Each explicit create request produces a
new voice variant linked to the same script, with an independent provider, voice label, style,
language, audio format, sample rate, speaking rate, pitch, and volume-gain snapshot. Voice variants
are never deduplicated by options.

The API creates and commits a pending track before publishing the separate `voice.generate` Celery
task. The task opens the existing async database session and composes `ScriptRepository`,
`VoiceTrackRepository`, `VoiceGenerationService`, and `LocalTTSProvider`. The provider writes the
artifact and returns validated duration, file size, SHA-256 checksum, storage key, and per-script-
section audio timing. Only then does the task commit the completed row.

The voice-track state machine is:

```text
pending → generating → completed
          ↖          ↘ failed
          └── retry ──┘
```

Generation failures persist `failed`, clear `completed_at`, and retain a useful error before the
Celery task reports failure. Retrying pending or failed work reuses the same row; generating and
completed tracks are returned without duplicate publication.

### Voice API

- `POST /api/v1/scripts/{script_id}/voice-tracks` creates a new pending variant, commits it, queues
  `voice.generate`, and returns `202 Accepted`.
- `GET /api/v1/scripts/{script_id}/voice-tracks` lists variants newest first, with compact status
  objects for unfinished rows and full artifact results for completed rows.
- `GET /api/v1/voice-tracks/{voice_track_id}` returns current status or the complete artifact
  result.
- `POST /api/v1/voice-tracks/{voice_track_id}/retry` re-enqueues the same pending or failed row with
  `202 Accepted`. Generating or completed tracks return `200 OK` without duplicate publication.

Completed task execution is idempotent: it does not invoke TTS again, alter the artifact, or create
another row. Reprocessing a non-completed track may overwrite only its own deterministic artifact
at `voice/{voice_track_id}/audio.wav`. `STORAGE_ROOT` defaults to `storage/videos`; API and worker
containers share the project bind mount, so both resolve voice artifacts beneath the same root.

### Local voice support and limitations

- The wired provider identifier is `local`.
- English and English region codes such as `en-US` are supported.
- WAV is supported. MP3 is explicitly rejected rather than receiving mislabeled WAV bytes.
- Voice labels and styles deterministically adjust synthetic tone frequency, cadence, and pauses.
- Speaking rate, pitch, volume gain, and sample rate affect PCM generation, but timing is synthetic
  and does not represent natural speech.
- `LocalTTSProvider` is an offline development adapter, not a speech engine. Kokoro remains an
  unimplemented placeholder, and no external provider selection is configured.
- Database commit and broker publication are not atomic. A crash after commit but before publish
  may leave pending work unqueued; synchronous broker failures are persisted and safely retryable.
- Artifact writing and database transactions are also not atomic. A failed or interrupted write
  can leave filesystem debris even though the database row is not committed as completed. Later
  cleanup/reconciliation work can remove orphaned artifacts.

## B-roll retrieval (Phase 6)

A completed `Script` may own multiple `BrollCollection` retrieval variants, and each collection
owns multiple `BrollAsset` candidate rows. Assets retain their source script-section order, so one
section may have several ranked candidates while every explicit retrieval request remains an
independent collection with its own options snapshot.

The API creates and commits a pending collection before publishing the separate `broll.retrieve`
Celery task. The worker uses the existing async session and composes `ScriptRepository`,
`BrollCollectionRepository`, `BrollAssetRepository`, `BrollRetrievalService`, and
`LocalMediaProvider`. The service deterministically derives section-keyword queries, validates
provider-neutral candidates, suppresses duplicates, persists candidate assets, and completes the
collection. No search runs inline in an HTTP request.

The collection lifecycle is:

```text
pending → searching → completed
          ↖         ↘ failed
          └─ retry ──┘
```

Assets use the independent lifecycle `candidate`, `selected`, `downloaded`, `rejected`, or
`failed`. Phase 6 creates candidates and supports explicit selection/rejection; it does not yet
download assets or create storage keys. Selecting an asset does not reject its siblings, and more
than one asset may remain selected for a script section.

### B-roll API

- `POST /api/v1/scripts/{script_id}/broll-collections` creates a new pending variant, commits it,
  queues `broll.retrieve`, and returns `202 Accepted`.
- `GET /api/v1/scripts/{script_id}/broll-collections` lists variants newest first, returning compact
  status objects for unfinished collections and complete results with assets after completion.
- `GET /api/v1/broll-collections/{collection_id}` returns current status or the completed collection
  and its persisted assets.
- `POST /api/v1/broll-collections/{collection_id}/retry` reuses a pending or failed row and returns
  `202 Accepted`; searching or completed rows return `200 OK` without duplicate publication.
- `GET /api/v1/broll-collections/{collection_id}/assets` lists assets by section order, descending
  relevance, and stable ID order.
- `GET /api/v1/broll-assets/{asset_id}` returns explicit candidate or artifact metadata.
- `POST /api/v1/broll-assets/{asset_id}/select` and `/reject` update only the requested asset.

Each create request intentionally produces a new collection, even when its options match another
variant. Completed task reruns are idempotent and do not call the provider again. Retrying an
unfinished collection preserves existing candidates; duplicate suppression uses provider and
external ID first, normalized source URL second, and a stable provider-neutral hash as fallback.

### Local media support and limitations

- `LocalMediaProvider` supports synthetic video and image metadata for English and English regional
  language codes.
- Candidate IDs, dimensions, bounded video durations, relevance ordering, and metadata are
  deterministic. Placeholder URLs use `https://local.invalid/...` and are intentionally not
  downloadable.
- The local provider marks results as safe synthetic placeholders. It performs no semantic media
  search, licensing verification, network request, or file download.
- Pexels, Pixabay, Unsplash, and other licensed providers are not integrated or configured.
- Database commits and Celery publication are not atomic. Synchronous broker failures are persisted
  on the same collection, but a crash after commit and before publication may leave pending work
  unqueued. A transactional outbox is not implemented.

The Phase 6 migration is part of the normal migration chain. Apply all migrations and verify the
project with:

```bash
docker compose exec api uv run --no-sync alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
docker compose ps
curl --fail http://localhost:8000/api/v1/health
```

Host PostgreSQL remains available at `localhost:5433`; API and worker containers connect to
PostgreSQL at `postgres:5432`.

## Video rendering (Phase 7)

A completed `Script` may own multiple `VideoRender` variants. Every render references exactly one
completed `VoiceTrack` belonging to that script and may reference one completed
`BrollCollection`. Voice tracks and B-roll collections can be reused by multiple renders. Each
render stores immutable option, subtitle-style, and completed timeline snapshots without placing
ORM objects or absolute filesystem paths in JSON.

The API creates and commits a pending render before publishing the separate `video.render` Celery
task. The worker opens the existing async database session and composes `ScriptRepository`,
`VoiceTrackRepository`, `BrollCollectionRepository`, `BrollAssetRepository`,
`VideoRenderRepository`, `VideoRenderService`, and `FFmpegVideoRenderer`. The service validates
input readiness, builds the deterministic timeline, and maps validated renderer metadata back to
the existing row. The renderer owns FFmpeg command execution and artifact validation.

The render lifecycle is:

```text
pending → rendering → completed
          ↖         ↘ failed
          └─ retry ──┘
```

Narration spans the completed voice-track duration. Subtitle items follow voice segment timing
when subtitles are enabled; overlaps among narration, subtitles, and visual layers are allowed.
When no usable local B-roll exists, FFmpeg renders a deterministic solid-color background at the
requested dimensions and FPS. Selected metadata-only Phase 6 assets have no storage key and are
therefore skipped without attempting their `local.invalid` URLs. The current compositor supports
at most the first usable local B-roll image or video layer; it does not download or combine a full
multi-asset visual sequence.

`FFmpegVideoRenderer` currently supports MP4 with H.264 video and AAC audio. The existing WAV voice
artifact is the primary audio stream. Optional single-pass loudness normalization targets the
requested LUFS value. Narration shorter than one second uses a deterministic resample-and-pad
fallback because FFmpeg `loudnorm` can emit non-finite samples for very short or silent inputs.
Disabling normalization passes narration through the filter graph unchanged before AAC encoding.

After rendering, `ffprobe` must report a positive duration and both video and audio streams. The
adapter also verifies a non-empty file and persists its measured duration, byte size, and SHA-256
checksum. Artifacts use relative keys beneath the shared `STORAGE_ROOT`:

```text
renders/{render_id}/output.mp4
```

API and worker containers use the same storage root and project bind mount. HTTP responses expose
only the relative key, never an absolute host/container path.

### Render API

- `POST /api/v1/scripts/{script_id}/renders` creates a new pending render variant, commits it,
  queues `video.render`, and returns `202 Accepted`.
- `GET /api/v1/scripts/{script_id}/renders` lists variants newest first, returning compact status
  objects for pending, rendering, and failed rows and complete artifact results for completed rows.
- `GET /api/v1/renders/{render_id}` returns the current status or completed render result.
- `POST /api/v1/renders/{render_id}/retry` reuses a pending or failed row and returns `202
  Accepted`. Rendering and completed rows return `200 OK` without duplicate publication.

Every explicit create request produces a distinct variant, even for identical options. Completed
task reruns and completed retries are idempotent: they do not invoke FFmpeg again, change existing
bytes/checksums, or create another row. Pending and failed retries preserve the render options and
timeline snapshots; processing rebuilds and validates the timeline before invoking the renderer.

### Rendering limitations and verification

- Rendering is local development infrastructure, not a production multi-layer compositor.
- MOV, WEBM, HEVC, VP9, Opus, and PCM are represented by contracts but are not currently accepted
  by the local adapter; unsupported combinations fail explicitly.
- Phase 6 does not download media. Only selected/downloaded B-roll with an actual readable local
  storage key can enter visual composition.
- Filesystem writes and database transactions are not atomic. A failed FFmpeg execution or later
  validation failure is never committed as completed, but a partial output artifact may remain for
  later cleanup.
- Database commit and Celery publication are also not atomic. Synchronous broker failures are
  persisted on the same render, while a crash after commit and before publish may leave pending
  work unqueued. A transactional outbox is not implemented.

Apply the complete migration chain and run local verification with:

```bash
docker compose exec api uv run --no-sync alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run pytest
docker compose config
```

The Docker image contains FFmpeg and ffprobe. Run the real renderer and worker-boundary checks with:

```bash
docker compose exec api uv run pytest tests/test_phase7_workflow.py -q
docker compose exec api uv run pytest tests/test_video_rendering_task.py -q
docker compose exec api uv run pytest tests/test_ffmpeg_video_renderer.py -q
docker compose ps
curl --fail http://localhost:8000/api/v1/health
```

Host PostgreSQL remains at `localhost:5433`; API and worker containers use `postgres:5432`.

## Publishing (Phase 8)

A completed `VideoRender` may own multiple `PublishJob` variants or attempts. Each job snapshots
the exact render artifact key, checksum, byte size, and duration when it is created, so later render
changes cannot silently alter the bytes that the publication intended to use. Jobs also preserve
normalized metadata and provider-neutral options. They never store OAuth tokens, refresh tokens,
API keys, or client secrets. Visibility defaults conservatively to `private`.

The API creates and commits a pending job before publishing the separate `publish.execute` Celery
task. The worker composes `VideoRenderRepository`, `PublishJobRepository`, `PublishingService`, and
the injected `PublishingProvider`. `PublishingService` owns render readiness, source snapshots,
scheduling checks, state transitions, cancellation, provider-result validation, and failure
persistence. Repository writes remain flush-only until the API or task transaction boundary commits.

The current `LocalPublishingProvider` is a deterministic offline development adapter. It derives
stable `local-youtube-*` remote IDs from the persisted provider-neutral input, returns synthetic
`https://publishing.local.invalid/...` URLs, and records `synthetic=true` and
`real_publication=false` in JSON-safe provider metadata. It never opens the render artifact, makes a
network request, resolves account credentials, or claims that a real YouTube upload exists.

The publishing lifecycle is:

```text
pending → publishing → published
   │          └──────→ failed
   └───────────────→ cancelled
failed ────────────→ cancelled
```

Failed jobs may retry using the same row and immutable source/intent snapshots. Published task
reruns and retries are idempotent and preserve remote identity, URL, timestamp, and provider
metadata. Publishing or published jobs cannot be cancelled, while cancelling an already-cancelled
job is idempotent. Cancellation does not perform remote deletion.

### Publishing API

- `POST /api/v1/renders/{video_render_id}/publish-jobs` creates a distinct pending attempt and
  returns `202 Accepted`. Due or unscheduled jobs are queued after commit.
- `GET /api/v1/renders/{video_render_id}/publish-jobs` lists attempts newest first with mixed status
  and complete published response shapes.
- `GET /api/v1/publish-jobs/{publish_job_id}` returns current status or the complete remote result.
- `POST /api/v1/publish-jobs/{publish_job_id}/retry` reuses pending/failed rows. Published and
  publishing rows return without duplicate dispatch; cancelled rows return a conflict.
- `POST /api/v1/publish-jobs/{publish_job_id}/cancel` cancels pending/failed jobs without enqueueing
  work or contacting a provider.

`scheduled_publish_at` records timezone-aware scheduling intent. A future-scheduled create or retry
is committed as pending but is not dispatched early. ClipForge intentionally has no durable
production scheduler yet: no polling loop, sleeping worker, or ETA dispatcher was introduced.

### Publishing limitations and verification

- Local publishing is a safe simulation, not YouTube integration. OAuth, uploads, remote status
  reconciliation, remote deletion, and real provider limits remain unimplemented.
- Database commits and broker publication are not atomic. A synchronous enqueue error is persisted
  as failed on the same job, but a crash after commit and before publication can leave pending work
  undispatched. A transactional outbox is not implemented.
- Future scheduled jobs require later durable scheduling infrastructure to dispatch automatically.

Apply the complete migration chain and verify Phase 8 with:

```bash
docker compose exec api uv run --no-sync alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run pytest
git diff --check
docker compose config
docker compose ps
curl --fail http://localhost:8000/api/v1/health
```

Host PostgreSQL remains at `localhost:5433`; API and worker containers use PostgreSQL at
`postgres:5432`.

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

## Roadmap

- Phase 1 — Backend Infrastructure: complete
- Phase 2 — Video Ingestion: complete
- Phase 3 — Video Analysis: complete
- Phase 4 — Script Generation: complete
- Phase 5 — Voice Generation: complete
- Phase 6 — B-roll Retrieval: complete
- Phase 7 — Video Rendering: complete
- Phase 8 — Publishing: complete
