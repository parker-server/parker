# Background Job Manager Scope

## Status

Design decided. PR 1 (groundwork) is implemented on the `background-job-lanes` branch; PR 2 and PR 3 are not started. See [Rollout](#rollout).

## Goal

`app/services/scan_manager.py` began as the library scan coordinator, but it now manages more than scans:

- library scans
- thumbnail generation
- cleanup jobs
- metadata rehydrate jobs

The file is already a database-backed queue worker in practice. User-triggered enrichment work, starting with ComicBookRoundup critic review lookups, should use that durable queue instead of FastAPI `BackgroundTasks`.

This note records how the manager's scope will be broadened and when it will be renamed.

## Why This Matters

Parker normally runs Uvicorn with multiple workers because a single worker can make the UI feel slow when scans, thumbnail work, metadata work, or other long operations are active.

Multi-process Uvicorn changes how background work must be designed:

- In-memory locks do not coordinate across workers.
- In-memory queues do not coordinate across workers.
- FastAPI `BackgroundTasks` are process-local and not durable.
- A worker can die or reload after marking state as pending but before finishing the background task.
- Important work should be claimed through database state.

The current job queue already handles several of these concerns:

- persisted job rows
- atomic transition from `pending` to `running`
- startup recovery for interrupted `running` jobs
- admin-visible job history
- centralized scheduling for long-running work

Today the external review endpoint (`GET /api/comics/{comic_id}/external-reviews`) claims the lookup row and then starts `refresh_external_reviews_for_comic` with `BackgroundTasks`. That runs in whichever Uvicorn worker served the request, so outbound requests to ComicBookRoundup cannot be rate-limited globally and are lost if the worker exits.

## Non-Goals

- Do not rename `scan_manager.py` or `ScanManager` in the same PR that adds external review jobs.
- Do not rename the `scan_jobs` table as part of this work.
- Do not introduce Celery, RQ, Redis, or another queue dependency unless Parker outgrows the local database-backed worker.
- Do not make library scans wait behind large numbers of interactive enrichment jobs.
- Do not let user-triggered enrichment jobs hammer external sites.

## Decisions

| Question | Decision |
| --- | --- |
| Same table or a new `background_jobs` table? | Same `scan_jobs` table, with generic target columns. A second table would mean two claim paths and two admin queries. |
| One enrichment thread or per-provider threads? | One enrichment thread. Revisit only when a second provider exists. |
| Where does provider rate limiting live? | In the enrichment lane of the job manager. The external review service keeps the cache/retry windows. |
| Are external review jobs visible in admin job history? | Visible in the live queue. Hidden from job history by default, with a job-type filter to show them (including failures). |
| Final name? | `BackgroundJobManager`, with `scan_manager` kept as a compatibility alias. |

## Schema Changes

One Alembic migration on `scan_jobs`:

- `target_type`: nullable string. `comic` for external review jobs. Room for `series`, `global`, etc. later.
- `target_id`: nullable integer.
- `payload_json`: nullable text holding provider-specific options.

`library_id` stays as-is. Existing library jobs keep using it, so current queries, the `library` relationship, and admin display are unaffected. New columns are only populated by job types that need them.

The migration also adds a **partial unique index** on `(job_type, target_type, target_id)` where `job_type = 'external_review_lookup'` and `status IN ('pending', 'running')`.

Reason: the existing enqueue methods (`add_task`, `add_thumbnail_task`, etc.) check for an active job and then insert. Across multiple Uvicorn workers, two requests can both pass that check. The partial index makes the database reject the second insert; the enqueue method catches `IntegrityError` and returns the existing job. SQLite supports partial indexes (Alembic: `op.create_index(..., sqlite_where=sa.text(...))`).

No `lane` column is stored. Lane membership is derived from `job_type` in code.

For ComicBookRoundup jobs:

- `job_type = "external_review_lookup"`
- `target_type = "comic"`
- `target_id = comic.id`
- `payload_json = {"provider": "comicbookroundup", "force": false}`

## Queue Lanes

The worker used to be a single serial loop with four copy-pasted priority queries. It is now one manager with a worker thread per lane. Lanes are defined next to `JobType` in `app/models/job.py`, so the admin jobs API can import them without loading the scanner:

```python
JOB_LANES = {
    "library": (JobType.SCAN, JobType.THUMBNAIL, JobType.CLEANUP, JobType.METADATA_REHYDRATE),
    "enrichment": (JobType.EXTERNAL_REVIEW_LOOKUP,),  # added in PR 2
}
```

- `_process_lane(lane)` (formerly `_process_queue`) walks the lane's job types in tuple order (which is the priority order), then does the same atomic claim as before.
- `start()` and `stop()` manage one thread per lane (`worker_threads`, keyed by lane). Interrupted-job recovery still runs once per `start()`.
- `_fix_stuck_libraries` runs only in the library lane.
- `JOB_TYPE_PRIORITY` in `app/api/jobs.py` is derived from `JOB_LANES`.
- PR 1 defines only the library lane. Every lane gets a polling thread, so the enrichment entry is added in PR 2 together with its job type.

External review lookups are short, network-bound, and usually triggered by a user viewing an issue. Their own lane means they never wait behind a scan pipeline, and scans never wait behind them.

## Multi-Process Uvicorn Behavior

Only the process that owns the existing manager lock (`scheduler.lock` in `app/main.py`) starts lane workers.

Other Uvicorn workers can still enqueue jobs and read job state because those operations go through the database.

The job claim path stays database-backed:

1. Find an eligible `pending` job for the lane.
2. Atomically update it to `running`.
3. Commit.
4. Execute the job outside the claiming session.

Because only one process runs the enrichment lane, it is the single place that makes outbound ComicBookRoundup requests. That is what makes an in-memory rate limiter correct.

## External Review Lookup Jobs

### Enqueue path

The endpoint keeps calling `ExternalReviewService.claim_refresh`, then calls `add_external_review_task(comic_id, provider)` instead of `background_tasks.add_task(...)`.

The two rows have separate jobs:

- **`external_review_lookups` row**: the cache. Owns match status, reviews, and the positive/negative/failed retry windows.
- **`scan_jobs` row**: the only lock on in-flight work. The partial unique index guarantees one active job per comic/provider.

Interaction with `PENDING_RETRY_AFTER` (2 minutes):

- If a job waits in the queue longer than 2 minutes, a later page view may re-claim the pending lookup. That is harmless: the enqueue returns the already-active job instead of creating a duplicate.
- If a process dies between claiming the lookup and inserting the job, the lookup sits in `pending` with no job. The 2-minute window lets the next page view claim and enqueue it again.

The comic detail page already polls the endpoint while status is `pending`, so no frontend changes are needed.

### Worker behavior

- If external reviews are disabled in settings when the job runs, complete it as skipped without a network request.
- If the comic no longer exists, complete it as skipped.
- Otherwise call the existing `ExternalReviewService.refresh_comic` logic.
- Job status:
  - `matched`, `no_reviews`, `no_match`, `ambiguous` → job `COMPLETED`.
  - `failed` (network/parse error) → job `FAILED` with the lookup's error message.
- Job summary fields:
  - provider
  - comic ID
  - issue display title (snapshot, so history still reads well if the comic is deleted)
  - match status
  - confidence score
  - review count stored
  - source issue URL, if one was found

### Rate limiting and site courtesy

- The enrichment lane enforces a minimum interval between ComicBookRoundup lookups (in-memory timestamp; a few seconds by default).
- Never enqueue during library scans.
- Only enqueue from explicit user navigation or refresh.
- One active job per comic/provider (partial unique index).
- Cache windows stay in `app/services/external_reviews.py` (30 days matched, 14 days negative, 1 day failed).

The worker treats external lookups as best-effort enrichment, not required metadata.

### Restart recovery

`_recover_interrupted_jobs` currently writes "Scan interrupted by server restart" for every job. It should use a message per job type. Interrupted external review jobs are marked `FAILED`; their lookup row stays `pending`, so the 2-minute window makes it retryable on the next page view.

## Admin Visibility

- `serialize_job` shows `-` in the library column for enrichment jobs and uses the summary's comic title as the target.
- Admin labels make clear these are enrichment jobs, not scan jobs.
- The live queue shows all lanes.
- Job history hides external review jobs by default. A job-type filter shows them, including failures. Every comic view that needs a refresh creates a row, so without this the 30-day history could be mostly review lookups.
- Existing `jobs.retention_days` cleanup applies to these rows unchanged.

## SQLite Contention

Protecting the parallel scan and thumbnail writers is the priority here. The enrichment lane must never make them wait.

### How much the writers tolerate

- The engine runs SQLite in WAL mode with a 60-second lock timeout (`app/database.py`). Reads don't block writers.
- The scan's metadata writer (`app/services/workers/metadata_writer.py`) is a separate process that commits in batches of 50. It does **not** retry: if it waits on the lock for more than 60 seconds, the scan fails.
- The thumbnail writer (`app/services/thumbnailer.py`) commits in batches of 25 and retries up to five times.

So the rule that matters: no other connection may hold the write lock for long.

### Findings

`ExternalReviewService.refresh_comic` holds the write lock only for milliseconds:

1. It creates the lookup or marks it `pending`, then commits.
2. It calls ComicBookRoundup with no write open. This step can take many seconds (10-second HTTP timeout, up to 10 candidate pages), but the lock is free throughout.
3. It saves the result and reviews in a single commit.

Moving lookups into the queue lowers contention. Today they run as `BackgroundTasks` in any Uvicorn worker, so several lookups can write in parallel. The enrichment lane runs at most one at a time, from one thread. Those writes are about the same size as the reading-progress and rating writes users already make during scans.

Pausing the enrichment lane while a library job is running was considered and rejected. Critic reviews would not load during long scans, and the writes are too small to matter.

### Guardrails

- **Rule: never hold a write across network I/O.** Enrichment jobs must commit any pending write before an outbound request and start new writes only after the response is in hand. This applies to every future provider, not just ComicBookRoundup.
- **Test for the rule.** A test uses a fake ComicBookRoundup client that fails if the session's underlying connection has an open transaction (`in_transaction`) when the lookup is called. This catches a future change that moves a commit or adds a flush before the network call.
- **Rollback fix in `refresh_comic`.** If the final commit fails (for example, a lock timeout), the `except` block currently updates the lookup and commits again without rolling back. SQLAlchemy rejects that with `PendingRollbackError`, the error escapes, and the lookup stays `pending` until the 2-minute retry. The `except` block should call `self.db.rollback()` first, then reload the lookup before recording `failed`. A test forces the final commit to raise `OperationalError` and checks that the lookup ends `failed` with the error recorded.

No extra lock-retry wrapper is planned for enrichment writes. Add one only if lock errors show up in testing.

## Rollout

### PR 1: groundwork (no behavior change)

- Migration: `target_type`, `target_id`, `payload_json`, and the partial unique index.
- Model updates in `app/models/job.py`.
- Lane refactor in `ScanManager` (`JOB_LANES`, `_process_lane`, multi-thread `start`/`stop`). The library lane behaves exactly as before; only the library lane is defined.

### PR 2: external review jobs

- `JobType.EXTERNAL_REVIEW_LOOKUP` and `add_external_review_task`.
- Worker handler, rate limiting, per-type recovery messages.
- Rollback fix in `refresh_comic`'s failure path (see [SQLite Contention](#sqlite-contention)).
- Endpoint switches from `BackgroundTasks` to the queue.
- Admin job display and the history job-type filter.

### PR 3: rename

- Rename `ScanManager` to `BackgroundJobManager`.
- Keep `scan_manager = background_job_manager` as an alias so scheduler, watcher, routes, and tests can migrate gradually.
- Update admin wording to "Background Jobs" where appropriate.

### Later / out of scope

- After PR 2: a health check for lane worker threads, so a dead lane is logged or restarted without going through `start()` (tracked in `TODO.md`).

- Renaming the `scan_jobs` table. Only if something forces it; it touches migrations, models, admin APIs, tests, and release notes.
- Moving series thumbnail regeneration (`POST /api/series/{series_id}/thumbnails`, currently `BackgroundTasks`) onto the queue as a `THUMBNAIL` job with `target_type = "series"`.

## Testing Plan

PR 1:

- Library lane claims jobs in the same priority order as before.
- Lane workers start and stop cleanly; only lane-owned job types are claimed by each lane.
- Migration upgrades and downgrades cleanly.

PR 2:

- External review jobs are de-duplicated per comic/provider, including the concurrent insert case (partial index → existing job returned).
- Repeated page requests do not enqueue duplicate jobs while one is pending or running.
- A pending lookup with no active job (stale after 2 minutes) can be re-claimed and re-enqueued.
- A restart marks interrupted external review jobs failed with a type-specific message, and the lookup is retryable afterwards.
- Disabled setting or deleted comic completes the job as skipped without a network call.
- `failed` lookup → `FAILED` job; other outcomes → `COMPLETED` with the expected summary fields.
- External review jobs do not block scan, thumbnail, cleanup, or metadata rehydrate jobs, and vice versa.
- Rate limiter enforces the minimum interval between lookups.
- No write is open on the session's connection when the ComicBookRoundup client is called (fake client asserts `in_transaction` is false).
- A failed final commit in `refresh_comic` (forced `OperationalError`) is rolled back and leaves the lookup `failed` with the error recorded, instead of raising `PendingRollbackError`.
- Admin queue API includes external review jobs; history excludes them by default and includes them with the filter.
- Browser test: the comic detail page moves from pending to loaded reviews without a reload.

PR 3:

- `scan_manager` alias still resolves to the same instance.
