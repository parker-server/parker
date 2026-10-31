# Comic Requests Scope

## Status

Draft. Not started. Open questions are listed at the end; the phase 1 shape below does not depend on them.

## Goal

Let readers ask the admin for comics that are not in the library, and let Parker close those requests by itself when the comics show up in a scan.

The inspiration is Seerr (formerly Overseerr), where users browse a movie and TV catalog and press **Request**. Parker has no outside catalog to browse, and adding one would cut against the "Filesystem is Truth" model. Parker does, however, already know where many gaps are. Requests should start from those gaps, so the Request button appears where Parker can see that something is missing.

## Product Principle

- A request is a note from a reader to the admin. It never changes comic archives or library metadata.
- Phase 1 makes no network calls and needs no API keys.
- Parker never guesses when matching. A request closes only on a confident match, using the same "never guess" rules as CBL matching.
- A request must never reveal content the requester cannot see, whether hidden by library access or by age rating.

## Non-Goals

- No outside catalog search (Metron, ComicVine) in phase 1. See [Phase 3](#phase-3-optional-outside-catalog-and-downloaders).
- No automatic downloading. Parker does not acquire files.
- No email delivery. Phase 1 status lives in the app.
- No new frameworks or queue dependencies. The work fits in the existing FastAPI, SQLAlchemy and Alpine.js patterns and the existing job queue.

## Where Requests Come From

| Source | Signal Parker already has | Entry point |
| --- | --- | --- |
| `missing` | Numbering gaps from `ComicInfo` `Count` (the logic behind `GET /api/reports/missing`) | Series and volume pages: "Missing #4, #7, #12" chips with **Request** and **Request all** |
| `cbl` | Unmatched CBL entries (series, number, volume, year) | Reading list detail: unmatched entries shown greyed out, each with **Request**, plus **Request all missing** |
| `follow` | `UserVolumeFollow` | Volume page: "Request new issues as they come out" toggle (a standing request, phase 2) |
| `search` | Navbar quick search or Advanced Search with no results | "Can't find it? Request it", prefilled from the query |
| `manual` | None | A plain form: series, start year, issue or range, notes |

### Prerequisite: Saving Unmatched CBL Entries

Today `CBLSource.last_match_summary` keeps only counts and a sample of 10 unmatched entries. The reading list entry point needs the full list. Options:

1. Re-parse the stored `.cbl` file (`CBLSource.stored_path`) and re-run matching when the page loads. No schema change, but it loads every comic on each view (see [Matching](#matching-and-fulfilment)).
2. Save unmatched entries in a small `cbl_source_unmatched` table, rewritten on each `cbl_source_service.rebuild()`. One extra table, cheap page loads.

Recommendation: option 2. `rebuild()` already runs after every scan, so the table stays current at no extra cost.

### Missing-Issue Logic for Readers

The missing-issues report is admin-only and builds a whole-library report. Readers need the same calculation for one volume. Pull the per-volume part (expected range from `Count`, minus existing plain issue numbers) into a shared helper in `app/core/comic_helpers.py` or a service, used by both the report and the volume page. Readers see gaps only for volumes they can already see.

## Concepts

- **Request target**: what is wanted, identified by normalized series name, optional volume start year, and optional issue number. With no issue number, the target is the whole volume or series.
- **Supporter**: a user who wants that target. Two users asking for the same thing become one target with two supporters. This gives the admin a demand count, which Seerr does not.
- **Range requests** ("#4–#7", "Request all missing") are split into one target per issue when created. This keeps merging and fulfilment simple: one target, one comic.
- **Standing request** (phase 2): a volume-level target that never closes. Each new matching issue alerts its supporters.

## Lifecycle

```
pending ──approve──▶ approved ──(scan match)──▶ fulfilled
   │                    │
   │                    └──admin "acquiring"──▶ acquiring ──(scan match)──▶ fulfilled
   ├──decline──▶ declined
   └──last supporter withdraws──▶ cancelled
```

- `pending` targets can also be fulfilled directly by a scan match. Approval is not required for auto-close.
- When the last supporter withdraws, the target becomes `cancelled`, unless the admin has already marked it `acquiring`.
- `declined` stores an admin note shown to supporters.
- A setting can auto-approve all requests, for small household servers where triage is overhead.

## Matching and Fulfilment

### When

After each library scan, alongside the existing CBL rebuild loop in `Scanner` (`app/services/scanner.py`, after Phase 3 cleanup), call a request resolver. As with the CBL loop, a failure there is logged and never fails the scan. Admins also get a **Recheck now** button that queues the same work.

### How

Reuse the matching approach in `app/services/cbl_matching.py`:

- `normalize_title()` for series names and `normalize_issue_number()` for numbers
- tiers: series + number + year, then series + number, never picking between ambiguous candidates

`match_cbl_entries()` loads every comic in the database to build its indexes. On the 73k-comic server database, the request resolver should avoid that cost:

1. Load the open targets (usually few).
2. Load `Series.id, Series.name` (thousands of rows, not tens of thousands) and normalize names in Python to find the series that could match.
3. Load comics only for those series.

The index-building and tier logic should be shared with `cbl_matching.py` (extract a helper) rather than copied.

A ComicVine ID from the comic's `Web` field (already detected in `app/api/comics.py`) can be the strongest match tier when a request carries one. Phase 1 requests rarely will, but CBL entries and phase 3 catalog requests may.

### Access Check on Fulfilment

The matcher works across all libraries. A match only counts as fulfilled **for a supporter** when that supporter can see the matched comic, through both library access and age rating.

If the comic lands somewhere a supporter cannot see:

- that supporter's view stays unchanged (`approved` or `acquiring`)
- the admin queue shows "In library, but not visible to N supporters", so the admin can grant access or decline

This needs status at two levels: the target's overall status, and a per-supporter `fulfilled_at`.

## Privacy and Access

- Creating a request never tells the user whether the comic exists somewhere they cannot see. If a request target already matches a hidden comic, it is created as a normal `pending` request, and the admin sees the access note above.
- Readers see only their own requests in phase 1. Whether readers can see and support each other's requests is an open question.
- Missing-issue chips appear only on volumes the reader can already open, and only list numbers, so nothing new is revealed.

## Data Model

New models in `app/models/comic_request.py` (named to avoid confusion with the HTTP `requests` library), plus an Alembic migration.

`comic_requests` (one row per target)

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer PK | |
| `series_name` | String | As entered or as found, for display |
| `series_key` | String, indexed | `normalize_title(series_name)` |
| `volume_year` | Integer, nullable | Start year of the run |
| `issue_number` | String, nullable | Null means the whole volume or series |
| `issue_key` | String, nullable | `normalize_issue_number(issue_number)` |
| `series_id` / `volume_id` | FK, nullable, `SET NULL` | Set when created from a series or volume page |
| `external_id` | String, nullable | For example `cv:4000-12345`, from CBL entries or phase 3 |
| `kind` | String | `issue`, `volume`, `series`, `standing` |
| `status` | String, indexed | `pending`, `approved`, `acquiring`, `declined`, `fulfilled`, `cancelled` |
| `admin_note` | Text, nullable | |
| `fulfilled_comic_id` | FK `comics.id`, nullable, `SET NULL` | |
| `created_at` / `updated_at` / `fulfilled_at` | DateTime | |

Unique constraint on (`series_key`, `volume_year`, `issue_key`, `kind`) among open targets, so new requests merge into an existing open target. SQLite allows this with a partial unique index (`WHERE status IN (...)`), or the service can do the check, as Parker does elsewhere.

`comic_request_supporters`

| Column | Type | Notes |
| --- | --- | --- |
| `request_id` | FK, `CASCADE` | PK part |
| `user_id` | FK, `CASCADE` | PK part |
| `source` | String | `missing`, `cbl`, `follow`, `search`, `manual` |
| `note` | Text, nullable | |
| `created_at` | DateTime | |
| `fulfilled_at` | DateTime, nullable | Per-supporter, see the access check |
| `seen_at` | DateTime, nullable | Clears the "ready" badge |

`users`: add `can_request` (Boolean, default True), following the pattern of `can_download`. Admins can always request.

Settings (through the existing settings service): `requests_enabled` (default off for existing installs), `requests_auto_approve`, and later a per-user quota.

## API Sketch

New `app/api/comic_requests.py`, following the existing router, `SessionDep` and `AdminUser` patterns:

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/requests/mine` | user | My requests, with status per target |
| `POST` | `/api/requests` | user | Create one or many targets (range or "all missing"); merges into existing open targets |
| `DELETE` | `/api/requests/{id}/support` | user | Withdraw my support |
| `POST` | `/api/requests/seen` | user | Clear the "ready" badge |
| `GET` | `/api/requests` | admin | Queue, grouped by series, sortable by demand or age, filterable by status |
| `PATCH` | `/api/requests/{id}` | admin | Approve, decline, mark acquiring, edit note |
| `POST` | `/api/requests/batch` | admin | The same actions for many targets |
| `POST` | `/api/requests/recheck` | admin | Queue a resolver run |
| `GET` | `/api/requests/export.csv` | admin | Wanted list for manual shopping or other tools |

Service logic lives in `app/services/comic_requests.py`. New routes should be plain `def` unless they await, per the async route conversion work in `docs/async-route-conversion-scope.md`.

## UI Surfaces

- **Series and volume pages**: missing-issue chips with Request buttons, with already-requested issues shown as "Requested".
- **Reading list detail** (CBL-derived lists): unmatched entries shown greyed out at their position, each with Request.
- **Search with no results**: a request link, prefilled.
- **My Requests page**: tabs for Open, Ready and Closed. Ready items link to the comic.
- **Navbar badge** when requests have been fulfilled and not yet seen.
- **Home rail**: "Your requests are ready", hidden when empty, and able to be reordered or hidden through the existing home rail customization.
- **Admin queue**: grouped by series with supporter counts, batch actions, the access note, and CSV export. The admin dashboard links to it with a pending count.

## Notifications

Phase 1 uses the badge, the home rail and the My Requests page only. Parker has no notification system today, and these cover the common case of a reader who opens Parker regularly.

Phase 2 can add one outgoing webhook setting (with Discord-compatible payloads), fired when targets are created and when they are fulfilled. Apprise would cover more services but adds a dependency, which needs an explicit decision.

## Rollout

### Phase 1: Request Queue

- Models, migration, `can_request`, `requests_enabled` setting
- Shared per-volume missing-issue helper
- Saved unmatched CBL entries
- Entry points: missing issues, unmatched CBL entries, search with no results, manual form
- Admin queue with approve, decline and acquiring
- Resolver after scans plus Recheck now, with the access check
- My Requests page, badge, home rail

### Phase 2: Demand and Standing Requests

- Supporter counts shown to readers (if readers can see each other's requests)
- Standing requests from Follow
- Per-user quotas
- Outgoing webhook

### Phase 3 (Optional): Outside Catalog and Downloaders

This is the step that gets closest to Seerr.

- Opt-in Metron or ComicVine search for comics Parker has no record of, with the API key in settings and rate limiting through the enrichment job lane (as with ComicBookRoundup)
- Send approved targets to Mylar3 or Kapowarr wanted lists, the way Seerr hands off to Sonarr and Radarr
- Requests carry `external_id`, so matching can use the ComicVine ID tier

Tradeoffs: network calls, API keys, rate limits, and a dependency on outside services. It should stay off by default.

## Testing

- Unit: target merging, range splitting, status changes, and matcher tiers, including ambiguous candidates never matching.
- Unit: the access check, where a match in a hidden library or above a supporter's age limit does not fulfil that supporter.
- API: permission checks (`can_request`, admin-only routes, `requests_enabled` off), plus no leak when requesting hidden content.
- Scanner: the resolver runs after scans, and a resolver error does not fail the scan.
- Performance: run the resolver against a scratch copy of the 73k-comic server database to confirm it stays fast.
- Playwright: request from missing-issue chips, admin approves, a scan adds the comic, and the requester sees it under Ready.

## Open Questions

| Question | Recommendation |
| --- | --- |
| Is this mainly for household or friends servers where the admin acquires comics by hand, or should Parker eventually feed Mylar3 or Kapowarr? | Build phases 1 and 2 for the manual case. Phase 3 stays optional, and phase 1 does not block it. |
| Should readers see and support each other's requests, Seerr-style? | Own requests only in phase 1. Shared visibility leaks reading interests between users, so make it an admin setting in phase 2 if wanted. |
| Should requests be on by default for new installs? | Off by default. The feature adds admin work, so admins should opt in. |
| Should a declined target block re-requests? | No. A new request after a decline creates a fresh `pending` target, and the admin sees the earlier decline note. |
