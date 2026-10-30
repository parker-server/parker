# Async Route Conversion Scope

## Status

Planned for its own release. Counts come from an AST scan of `app/` on 2026-10-30, after 0.1.46.

## Goal

Make a function `async def` only when it awaits something. Every route that never awaits becomes plain `def`, so its blocking database calls run in the threadpool instead of on the event loop.

180 of the 204 `async def` functions under `app/` never await anything, so `async` gains them nothing. 124 of those 180 also make blocking database calls on the event loop, the same pattern behind the 0.1.45 outage, when scrolling the Batman Vol 1 cover gallery through Cloudflare froze the site.

## Non-Goals

- Do not switch to an async database driver or async SQLAlchemy sessions.
- Do not change route behavior, paths, or responses. For most routes the change is deleting the word `async`.
- Do not convert only the busiest pages. That leaves the admin pages with the same risk and keeps the codebase split between two styles.

## Background: `async def` vs `def`

In FastAPI, `async def` promises the function won't block, so it runs on the event loop: the one thread that handles every request in a worker. Plain `def` runs in a threadpool (40 threads per worker), where a slow call ties up only its own thread.

`async` only helps when the function awaits something, like an async HTTP client or an uploaded file. Parker uses regular blocking SQLAlchemy, so these functions never await anything. Marking them `async` saves a few microseconds of thread handoff per call; in exchange, any slow database wait freezes the whole worker.

The git history gives no reason for the pattern. `get_current_user` was `async` from the first commit of `deps.py` (`6fecb18`, Nov 2025), most likely following the style of FastAPI tutorials. Some modules are already plain `def` throughout, so the codebase is split between the two styles.

How it caused the outage: a blocking call on the event loop is harmless while each query takes under a millisecond. It became a deadlock when two things happened together:

1. Cover downloads each held a pool connection for the whole file transfer, and Cloudflare's many parallel requests used up all 15 connections in a worker.
2. The next request waited for a connection on the event loop. That stopped the worker from running the cleanup that hands connections back, so each wait ran its full 30 seconds, one after another, for about 9 minutes.

## Audit Results

Async functions that never await, by module:

| Module | Never await | Use the database |
| --- | ---: | ---: |
| `routers/pages.py` | 36 | 7 |
| `routers/admin.py` | 20 | 3 |
| `api/libraries.py` | 17 | 16 |
| `api/users.py` | 12 | 11 |
| `routers/opds.py` | 12 | 12 |
| `api/comics.py` | 8 | 8 |
| `api/series.py` | 8 | 8 |
| `api/reports.py` | 7 | 7 |
| `api/progress.py` | 6 | 5 |
| `api/jobs.py` | 5 | 5 |
| `api/volumes.py` | 5 | 5 |
| `api/bookmarks.py` | 4 | 4 |
| `api/reading_lists.py` | 4 | 4 |
| `api/stats.py` | 4 | 4 |
| `main.py` | 4 | 2 |
| `api/auth.py` | 3 | 2 |
| `api/cbl_sources.py` | 3 | 3 |
| `api/collections.py` | 3 | 3 |
| `api/deps.py` | 3 | 0 |
| `api/insights.py` | 3 | 3 |
| `api/saved_searches.py` | 3 | 3 |
| `api/tasks.py` | 3 | 2 |
| `api/search.py` | 2 | 2 |
| `api/timelines.py` | 2 | 2 |
| `api/batch.py` | 1 | 1 |
| `api/people.py` | 1 | 1 |
| `api/reader.py` | 1 | 1 |
| **Total** | **180** | **124** |

The API modules carry most of the risk: in `libraries.py`, `opds.py`, `users.py`, `series.py` and `comics.py` nearly every async function hits the database. `pages.py` and `admin.py` have the most async functions, but most only render templates, so only 10 of their 56 make database calls.

These modules already use plain `def` throughout: `home.py` (32 functions), `cover_murals.py` (23), `promoted_rails.py` (11), `pull_lists.py` (10) and `smart_lists.py` (7).

How the counts were made: a function counts as using the database if it takes a `db` argument or calls `db.`, `session.` or `.query`. Templates rendered by `pages.py` may also query through lazy loads, so 124 is a lower bound.

## The 24 That Do Await

17 are correctly `async`. The other 7 are admin-only CBL and upload routes.

| Functions | Count | What they await | Verdict |
| --- | ---: | --- | --- |
| External HTTP clients in `app/services/`: CBL catalog (7), CBL source (3), ComicBookRoundup (3), enrichment (1), external reviews lookup (1) | 15 | `httpx.AsyncClient` requests | Correct as they are |
| `enforce_required_password_change` (`main.py`) | 1 | `call_next`, with its query in the threadpool | Correct; middleware must be async |
| `get_version_check` (`stats.py`) | 1 | `asyncio.to_thread` for the slow part | Correct; already the right pattern |
| `upload_avatar` (`users.py`), `upload_cbl_source` (`cbl_sources.py`) | 2 | `await file.read()` | Rework: plain `def` and read `file.file` directly |
| `import_cbl_source_from_url`, `refresh_cbl_source` (`cbl_sources.py`), `import_catalog_file` (`cbl_catalog.py`) | 3 | HTTP downloads, then rebuild and commit | Rework: database work on the event loop, and the connection is held while the download runs |
| `browse_catalog`, `preview_catalog_file` (`cbl_catalog.py`) | 2 | GitHub fetch of the catalog | Rework: no queries of their own, but the admin check's query leaves the request's connection checked out for the whole GitHub fetch |

All 7 are rare admin actions, so they are low risk. The fix for the HTTP ones is to keep the `await`, end the transaction with `release_db_connection(db)` before the download starts, and move any later database work into `run_in_threadpool`.

## Conversion Plan

Batches, busiest pages first. Run the related tests after each batch.

- [ ] `routers/pages.py` (36)
- [ ] `api/series.py` (8), `api/comics.py` (8), `api/volumes.py` (5), `api/progress.py` (6), `api/reader.py` (1)
- [ ] `routers/opds.py` (12)
- [ ] `api/libraries.py` (17), `api/users.py` (12)
- [ ] `routers/admin.py` (20), `api/reports.py` (7), `api/jobs.py` (5), `api/tasks.py` (3), `api/stats.py` (4)
- [ ] The rest of `app/api/`: bookmarks, reading lists, collections, saved searches, auth, search, timelines, batch, people, CBL sources (26)
- [x] `api/insights.py` (3), converted early with the chemistry query speed-up
- [ ] The 7 CBL and upload routes from the table above
- [ ] Full test suite

Leave alone: the 5 `async` functions in `main.py` (the startup and shutdown handler, two exception handlers, `/health`, and the middleware), and the 3 remaining in `deps.py` (`get_token_hybrid`, `get_token_optional`, `get_current_active_superuser`), which do no I/O.

## Checked, No Blockers

- No route code relies on a running event loop: no `create_task` or `ensure_future`. The one `get_running_loop` call, in `services/external_reviews.py`, already handles being called from either a thread or the loop.
- `BackgroundTasks` (one use, `regenerate_thumbnails` in `series.py`) works the same from plain `def`.
- No app code awaits a route function directly.
- Only `tests/api/test_series.py` calls a route function with `asyncio.run` (2 calls), which is easy to update.

## Watch For While Converting

**Shared in-memory state.** On the event loop, a function with no `await` runs start to finish before any other request's code runs, so "read a shared value, then change it" can't be interrupted. In threads, two requests can run that code at the same moment. Python makes each single dict operation safe, but not a sequence of them without a lock:

- two threads add 1 to a shared counter and one addition is lost
- two threads both find a cache empty and both do the slow work to fill it (usually harmless)
- a multi-step update is interleaved halfway through

Only module- and class-level state is shared. Local variables, the request's DB session and objects made during the request belong to one request. What the check of Parker found:

- Settings cache (`settings_loader.py`): already locked, since scanner threads and existing `def` routes use it.
- `lru_cache` (comic helpers, archive page lists, build info): thread-safe.
- Scan name caches (`person_cache`, `collection_cache`): one per service object, not shared.
- CBL catalog cache (`CBLCatalogService._cache`): shared at class level, but its routes stay `async`; a race would only fetch twice.
- Password-change cache (`core/security.py`): used on the event loop, plus single dict operations from the admin endpoints.

`home.py`, `cover_murals.py`, pull lists and smart lists already run in threads today. So this is a check while converting, not a known problem: any converted function that changes module-level state needs a lock, or must be harmless when two requests race.

**The threadpool limit.** 40 blocking calls at once per worker. With 4 workers and 15 connections each, the pool runs out first.

## Already Fixed in 0.1.45

The paths that caused the outage are already off the event loop and are not counted above.

- Password-change middleware: its user lookup runs in the threadpool, and users who don't need to change their password are remembered for 30 seconds per worker, so most requests skip the query.
- Auth and access checks: `get_current_user`, `get_current_user_optional` and the four `get_secure_*` checks in `deps.py` are plain `def`.
- File endpoints: cover thumbnail, comic download, OPDS download, OPDS cover and avatar are plain `def`, and give their connection back through `release_db_connection` before the file is sent.

Verified on a small app built the same way, with a 2-connection pool: before the fix, a status endpoint stalled 17.6 seconds and 6 of 8 slow covers failed; after it, 16 of 16 succeeded and the status endpoint answered in under 0.05 seconds.
