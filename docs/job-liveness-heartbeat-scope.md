# Job Liveness Heartbeat Scope

## Status

Draft implementation note for a future follow-up to the admin Active Queue.

## Goal

The admin Active Queue already shows the macro job state: queued, running, job type, library, queue position, and elapsed queued/running time. A follow-up should answer one additional question:

> Is this long-running job still being actively managed, or does it look stale?

This should be implemented as liveness tracking only. It should not attempt fine-grained scan progress, per-file progress, percent complete, or exact worker location inside the scan pipeline.

## Non-Goals

- Do not add per-comic or per-file progress writes.
- Do not make scan, metadata, or thumbnail worker processes write heartbeat/progress state.
- Do not report a percentage complete unless a future job type can provide a cheap and reliable total.
- Do not add internal scan sub-phases unless they provide new user-facing truth beyond the existing job type.
- Do not compromise the current low-contention parallel scan/write model.

## Rationale

Scanning is a cornerstone of Parker and currently works well in parallel mode because CPU work and database writes are carefully separated. Metadata extraction and thumbnail generation use worker processes for CPU-bound work and dedicated writer processes for database updates. Any liveness system must preserve that property.

SQLite WAL mode allows readers to continue during writes, but it still permits only one writer at a time. Heartbeat writes therefore need to be opportunistic telemetry, not critical-path writes. If a heartbeat collides with a metadata or thumbnail writer batch, the heartbeat should lose quickly and skip that beat.

## Proposed Data Model

Add minimal liveness fields to `scan_jobs`:

- `last_heartbeat_at`: nullable datetime, updated while a job is running.
- `heartbeat_message`: nullable short string, optional human-readable liveness note.

Avoid adding `progress_current` / `progress_total` in this pass. Those fields imply a precision we do not intend to provide yet.

Optional later additions:

- `heartbeat_missed_count`: probably not needed if staleness can be derived from timestamps.
- `progress_phase`: only if we identify meaningful coarse phases beyond `job_type`.

## Write Strategy

Heartbeat writes must be:

- Manager-owned: only `ScanManager` writes liveness fields.
- Low cadence: start at 60 seconds. Do not go below 30 seconds without a measured reason.
- Tiny: single-row `UPDATE scan_jobs ... WHERE id = ?`.
- Short transaction: immediate commit, no relationship loading, no object graph updates.
- Best-effort: if the database is locked, skip the heartbeat.
- Non-retrying: do not use terminal-state retry behavior for heartbeat writes.
- Non-fatal: heartbeat failure must never fail or slow a job.

Do not reuse `_safe_job_update` for heartbeat writes. `_safe_job_update` is for important terminal or state-changing writes and intentionally retries. It also mutates `completed_at`, so it is the wrong abstraction for non-terminal liveness.

Introduce a separate helper with the opposite behavior, such as:

```python
def _try_job_heartbeat(job_id: int, message: str | None = None) -> bool:
    ...
```

This helper should use a very short SQLite write timeout, ideally via a dedicated heartbeat engine/session or a low-level connection configured with a small timeout. The application-wide `SessionLocal` currently has a longer SQLite timeout and should not be used if that would allow heartbeat writes to wait behind critical writer batches.

Suggested timeout: 100-250ms.

## Runtime Shape

Wrap long-running job bodies from `ScanManager`, not internal workers:

```python
with heartbeat_loop(job_id, message="Scanning library"):
    results = scanner.scan_parallel(...)
```

The heartbeat loop should:

- Start after the job is marked running.
- Attempt a heartbeat immediately or after the first interval.
- Repeat at the configured cadence while the wrapped operation is still active.
- Stop cleanly when the job finishes or fails.
- Never raise into the job execution path for lock/contention failures.

The heartbeat loop should be disabled for jobs that are not running.

## UI Behavior

Extend the Active Queue payload with:

- `last_heartbeat_at`
- `heartbeat_message`
- derived liveness metadata if useful, or derive it client-side

Suggested labels:

- `Active`: heartbeat is recent.
- `Starting`: job is very new and has no heartbeat yet.
- `Possibly stalled`: running job has no heartbeat within the stale threshold.

Suggested thresholds:

- Heartbeat cadence: 60 seconds.
- Stale threshold: 5 minutes.

The stale threshold should tolerate missed beats. A lock conflict, busy writer process, or short-lived worker delay should not produce a false alarm.

## Lock Contention Expectations

If heartbeat and a writer process collide:

- If the writer has the lock, heartbeat waits only briefly and skips if locked.
- If heartbeat gets the lock first, it should hold it for only a single indexed row update and immediate commit.
- Writer process retry logic remains the protection for critical write batches, but heartbeat should be designed so collisions are rare and microscopic.

Heartbeat writes must never intentionally exercise writer retry paths. They cannot guarantee they will never win a write-lock race, but they can guarantee they do almost nothing while holding the lock.

## Testing Plan

Add tests for:

- Heartbeat updates `last_heartbeat_at` for a running job.
- Heartbeat does not mutate terminal fields such as `completed_at`.
- Heartbeat returns `False` or otherwise skips cleanly when the database is locked.
- A skipped heartbeat does not fail the job runner.
- Queue API exposes heartbeat fields.
- Active Queue UI renders recent/stale states correctly.

For lock behavior, include a test that holds an SQLite write transaction open while `_try_job_heartbeat` runs. Assert that the heartbeat exits quickly and harmlessly rather than waiting on the normal database timeout.

## Rollout Plan

1. Add migration and model fields.
2. Add the best-effort heartbeat helper with short timeout behavior.
3. Add a manager-owned heartbeat wrapper in `ScanManager`.
4. Expose liveness fields through the queue endpoint.
5. Render Active/Starting/Possibly stalled states in the Active Queue.
6. Validate lock-contention behavior before wiring heartbeat around long-running jobs.

## Open Questions

- Should the heartbeat cadence be configurable, or fixed at 60 seconds initially?
- Should staleness be derived only in the UI, only in the API, or both?
- Should heartbeat messages be static per job type, or should we include very coarse manager-level notes?
