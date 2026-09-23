# Background Job Manager Scope

## Status

Draft design note for a future refactor. This is not part of the first ComicBookRoundup review implementation.

## Goal

`app/services/scan_manager.py` began as the library scan coordinator, but it now manages more than scans:

- library scans
- thumbnail generation
- cleanup jobs
- metadata rehydrate jobs

The file is already a database-backed queue worker in practice. Future user-triggered enrichment work, such as external critic review lookups, should use that durable queue shape instead of FastAPI `BackgroundTasks`.

This note captures the direction for broadening the manager's scope and eventually giving it a more accurate name.

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

External review lookups are a natural future fit for that system.

## Non-Goals

- Do not rename `scan_manager.py` as part of the first external review feature.
- Do not rename the `scan_jobs` table casually in the same pass as adding external reviews.
- Do not introduce Celery, RQ, Redis, or another queue dependency unless Parker outgrows the local database-backed worker.
- Do not make library scans wait behind large numbers of interactive enrichment jobs.
- Do not let user-triggered enrichment jobs hammer external sites.

## Proposed Direction

Broaden the current `ScanManager` into a general background job manager.

Possible names:

- `JobManager`
- `BackgroundJobManager`
- `QueueManager`

Recommended name: `BackgroundJobManager`.

It says what the object does without tying it to one job type or to implementation details.

For compatibility, keep a module-level alias for a while:

```python
background_job_manager = BackgroundJobManager()
scan_manager = background_job_manager
```

This lets scheduler, watcher, tests, and existing route code migrate gradually.

## External Review Lookup Jobs

External review lookups should eventually become a real persisted job type.

Possible job type:

```python
class JobType(str, enum.Enum):
    EXTERNAL_REVIEW_LOOKUP = "external_review_lookup"
```

The job should identify its target comic and provider. The current `scan_jobs` model only has `library_id`, so this likely needs either:

- dedicated nullable fields such as `comic_id` and `provider`, or
- generic fields such as `target_type`, `target_id`, and `payload_json`.

Recommended long-term shape:

- `target_type`: `library`, `comic`, `global`, etc.
- `target_id`: nullable integer.
- `payload_json`: nullable JSON/text for provider-specific options.

This avoids adding a new column for every future job target.

For ComicBookRoundup:

- `target_type = "comic"`
- `target_id = comic.id`
- `payload_json = {"provider": "comicbookroundup", "force": false}`

The worker would call the same service-level refresh logic currently used by the endpoint, but the endpoint would enqueue a job instead of starting a FastAPI background task.

## Queue Lanes And Priority

The current worker is effectively a single serial queue with a fixed priority order:

1. scan
2. thumbnail
3. cleanup
4. metadata rehydrate

External review lookups are different from those jobs:

- They are short.
- They are network-bound.
- They are usually triggered by an active user viewing an issue.
- They should be rate-limited and cached.
- They should not block scan pipelines.
- They should not sit for a long time behind a scan pipeline.

Recommended direction: keep one manager, but introduce lanes.

Possible lanes:

- `library`: scan, thumbnail, cleanup, metadata rehydrate.
- `interactive_enrichment`: external review lookup and similar user-triggered enrichment.

Each lane can have its own worker thread under the manager-lock-owning Uvicorn process.

This preserves centralized ownership while avoiding a bad user experience where an external review lookup waits behind a long scan.

## Multi-Process Uvicorn Behavior

Only the process that owns the existing manager lock should start queue workers.

Other Uvicorn workers can still enqueue jobs and read job state because those operations go through the database.

The job claim path must remain database-backed:

1. Find an eligible `pending` job.
2. Atomically update it to `running`.
3. Commit.
4. Execute the job outside the claiming session.

This is the core protection against duplicate workers or accidental duplicate processing.

For external review lookups, also keep provider-level cache constraints:

- Unique lookup cache row per `(comic_id, provider)`.
- Only one active lookup job per `(comic_id, provider)`.
- Pending/stale retry windows for interrupted jobs.
- Negative-result retry windows for no match, no critic reviews, ambiguous, and failed states.

## Rate Limiting And Site Courtesy

External review jobs should be conservative by default.

Recommended limits:

- Never enqueue during library scans.
- Only enqueue from explicit user navigation or refresh.
- De-duplicate active jobs per comic/provider.
- Cache positive results for a longer window, such as 30 days.
- Cache negative or empty results for a medium window, such as 14 days.
- Use a short stale-pending retry window for interrupted workers.
- Consider a provider-level minimum interval between outbound requests if live usage shows bursts.

The job worker should treat external lookups as best-effort enrichment, not required metadata.

## Admin Visibility

External review jobs should appear in the admin job history once they move into the persisted job system.

Useful fields in the job summary:

- provider
- comic ID
- issue display title
- match status
- confidence score
- review count stored
- source issue URL, if one was found
- error message, if failed

The admin queue should make it clear that these are enrichment jobs, not scan jobs.

## Naming And Migration Plan

Avoid a large rename in the same change that introduces external review jobs.

Suggested staged rollout:

1. Add any generic job fields needed for non-library targets.
2. Add `EXTERNAL_REVIEW_LOOKUP` as a job type.
3. Add queueing and worker handling while keeping `ScanManager` named as-is.
4. Move the external review endpoint from `BackgroundTasks` to persisted jobs.
5. Update admin job display labels to use "Background Jobs" language where appropriate.
6. Rename `ScanManager` to `BackgroundJobManager` in code.
7. Keep `scan_manager` as a compatibility alias.
8. Later, consider whether the `scan_jobs` table should be renamed.

Renaming the table should be its own follow-up because it touches migrations, models, admin APIs, tests, release notes, and operational expectations.

## Testing Plan

Add coverage for:

- External review jobs are de-duplicated per comic/provider.
- Repeated page requests do not enqueue duplicate jobs while one is pending or running.
- A stale pending external review job can be retried.
- A worker crash/restart marks interrupted jobs failed or retryable according to job type.
- External review jobs do not block scan, thumbnail, cleanup, or metadata rehydrate pipelines.
- Admin job APIs include external review job summaries.
- The comic detail page observes queued/running/completed external review job state without requiring a page reload.

## Open Questions

- Should enrichment jobs use the same `scan_jobs` table with generic target fields, or a new `background_jobs` table?
- Should interactive enrichment get one worker thread, or should provider-specific workers exist?
- Should provider-level rate limiting live in the job manager, the external review service, or both?
- Should failed external review jobs be visible in admin job history by default, or hidden unless diagnostics are expanded?
- Should the eventual rename be `BackgroundJobManager`, `JobManager`, or something shorter?
