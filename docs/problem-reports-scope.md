# Problem Reports Scope

## Status

Draft. Not started. Open questions are listed at the end; the phase 1 shape below does not depend on them.

## Goal

Let readers tell the admin when something is wrong with a comic they can already open: a bad scan, missing pages, pages out of order, a file that will not open, or wrong metadata. The admin gets one queue of problems to fix, and the reader learns when their report has been dealt with.

The inspiration is Seerr (formerly Overseerr), where a warning icon on a movie or TV page opens a **Report an Issue** modal. In Parker, "issue" already means a comic issue, so the feature is called **Report a Problem** in the UI and `ProblemReport` in code. The admin **Reports** section (`app/api/reports.py`: missing issues, duplicates, corrupt files) keeps its name and is a different thing.

This is the companion to [Comic Requests](comic-requests-scope.md): requests are about comics Parker does not have, problem reports are about comics it does.

## Product Principle

- A report is a note from a reader to the admin. It never changes comic archives or library metadata.
- Parker never closes a report on its own. A file change found by a scan is shown as a hint, and the admin decides.
- A report must never reveal content the reporter cannot see, whether hidden by library access or by age rating.
- Reporting should take a few seconds. Fields the reader has to fill in are kept to the minimum.

## Non-Goals

- No in-app metadata editing by readers. "Wrong metadata" is a report, not an edit.
- No automatic fixes, re-downloads or file replacement.
- No email delivery. Phase 1 status lives in the app.
- No reports on series, volumes or reading lists in phase 1. Gaps in a run belong to Requests; grouping problems are rare enough to go under a comic's "Other".
- No new frameworks or dependencies.

## Entry Points

| Where | How | Prefilled |
| --- | --- | --- |
| Reader toolbar (`app/templates/reader/partials/_toolbar.html`) | Warning icon button that opens the modal over the reader | Comic and current page number |
| Comic detail page (`app/templates/comics/comic_detail.html`) | Small warning icon next to the existing actions | Comic |

Both use the Lucide `triangle-alert` icon, the same warning-triangle idea as Seerr. The comic page draws it with the `lucide_icon` macro. The reader toolbar draws its 16 existing icons as inline SVGs rather than through the macro, so its report button copies the `triangle-alert` paths inline to match.

The reader is the more useful entry point. Missing pages, pages out of order and bad scans are found while reading, and the page number saves the admin from hunting for the spot.

Both entry points are hidden when the feature is off or the user cannot report.

## Categories

| Key | Label | Typical cases |
| --- | --- | --- |
| `scan_quality` | Bad scan quality | Blurry, low resolution, watermarked, cropped |
| `missing_pages` | Missing pages | Story pages, covers or back matter absent |
| `page_order` | Pages out of order or duplicated | Wrong page sorting, repeated pages |
| `broken` | Won't open or broken pages | Archive errors, pages that fail to render |
| `metadata` | Wrong metadata | Title, number, credits, date, cover choice |
| `wrong_comic` | Wrong comic | The file is a different issue than it claims |
| `other` | Other | Anything else |

The keys are stored; labels live in one place in the service so the modal and the admin page agree.

### Modal Fields

- **Category** (required): radio list, as in Seerr.
- **Page** (optional): number input, prefilled from the reader. Hidden for `metadata` and `wrong_comic`, where it does not help.
- **Details** (required only for `other`, otherwise optional): free text, capped at 1,000 characters.

## Lifecycle

```
open ──resolve──▶ resolved
  │
  ├──dismiss──▶ dismissed
  └──reporter withdraws──▶ (deleted)
```

- `resolved` and `dismissed` can carry an admin note shown to the reporter.
- A reporter can withdraw their own open report. Since nothing else refers to it, the row is deleted.
- The admin can reopen a resolved or dismissed report if the fix did not work.
- Each user can have one open report per comic and category. Reporting again updates that report's details and page instead of adding a row.

## Link to Scanning

When a scan updates a comic that has open reports, the admin usually replaced the bad file. Parker records this but does not close the report.

- In `Scanner` (`app/services/scanner.py`), the "changed file -> update" branch already decides that a known comic's file changed. After the update batch is processed, set `file_changed_at` on open reports for those comic IDs with one `UPDATE ... WHERE comic_id IN (...) AND status = 'open'`.
- The admin queue shows these as "File updated since report", with a filter for them.
- As with the CBL rebuild loop, a failure here is logged and never fails the scan.

When a comic is deleted (its file is gone), its reports are kept. `comic_id` is set to null and the report keeps a short label (series, number, path) captured when it was created, so the admin can still tell what it was about. The queue shows these as "Comic removed". This also covers an admin replacing a file under a new name, where the old comic row is deleted and a new one is created.

## Privacy and Access

- Users can only report comics they can open. The create endpoint uses the same library access and age-rating check as bookmarks (`ensure_bookmark_comic_access` in `app/api/bookmarks.py`). That check should move to a shared helper in `app/core/comic_helpers.py` rather than be copied.
- Readers see only their own reports.
- If several users report the same comic and category, the admin sees one grouped row with a count and each reporter's details. Readers never see each other's reports.

## Data Model

New model in `app/models/problem_report.py`, plus an Alembic migration.

`problem_reports`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer PK | |
| `comic_id` | FK `comics.id`, nullable, `SET NULL`, indexed | Null after the comic is deleted |
| `comic_label` | String | Series, number and relative path at creation, for display after deletion |
| `user_id` | FK `users.id`, `CASCADE`, indexed | |
| `category` | String | One of the category keys |
| `page_index` | Integer, nullable | Zero-based, matching the reader; shown one-based |
| `message` | Text, nullable | |
| `status` | String, indexed | `open`, `resolved`, `dismissed` |
| `admin_note` | Text, nullable | |
| `resolved_by_id` | FK `users.id`, nullable, `SET NULL` | |
| `file_changed_at` | DateTime, nullable | Set by the scanner hook |
| `created_at` / `updated_at` / `resolved_at` | DateTime | |

The "one open report per user, comic and category" rule is checked in the service, as Parker does elsewhere. A partial unique index (`WHERE status = 'open'`) is an option if the check proves leaky.

`users`: add `can_report` (Boolean, default True), following the pattern of `can_download`. Admins can always report.

## Settings

Settings categories come from the defaults list in `SettingsService` (`app/services/settings_service.py`), so a new category needs only new keys and a line in `categoryDescription()` in `app/templates/admin/settings.html`.

Add a **`features`** category ("Optional features readers can use."):

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `features.problem_reports_enabled` | bool | `true` | Shows the report buttons and enables the API |

When the setting is off, the buttons are hidden and the reader endpoints return 404, following `_ensure_opds_enabled()` in `app/api/users.py`. Admin endpoints stay available so existing reports can still be worked through.

The planned `requests_enabled` setting from the Requests scope should live in the same category.

## API Sketch

New `app/api/problem_reports.py`, following the existing router, `SessionDep` and `AdminUser` patterns. Service logic lives in `app/services/problem_reports.py`. Routes are plain `def` unless they await, per `docs/async-route-conversion-scope.md`.

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/problems/categories` | user | Category keys and labels for the modal |
| `POST` | `/api/problems` | user | Create a report, or update my open one for that comic and category |
| `GET` | `/api/problems/mine` | user | My reports with status and admin note |
| `GET` | `/api/problems/comic/{comic_id}` | user | My reports for one comic, for the comic page and reader |
| `DELETE` | `/api/problems/{id}` | user | Withdraw my open report |
| `GET` | `/api/problems` | admin | Queue grouped by comic and category, filterable by status, category and "file updated" |
| `GET` | `/api/problems/summary` | admin | Open count, for the nav and dashboard badge |
| `PATCH` | `/api/problems/{id}` | admin | Resolve, dismiss, reopen, edit note |
| `POST` | `/api/problems/batch` | admin | The same actions for a group or a selection |

## UI Surfaces

- **Report modal**: one shared partial in `app/templates/partials/`, used by the reader and the comic page, modelled on the existing modals (for example `add_pull_list_modal.html`).
- **Comic page and reader**: if the user already has an open report on this comic, the icon shows a dot and the modal lists it with **Withdraw**. A resolved report shows its admin note once.
- **My Reports**: a section on the user dashboard (`/user/dashboard`) with Open and Closed tabs.
- **Admin Problems page** (`app/templates/admin/problems.html`):
  - Grouped rows: comic cover, series and number, category, report count, newest page number, age.
  - Flags: "File updated since report", "Comic removed".
  - Row actions: **Open in reader at page X**, **Open comic**, **Resolve**, **Dismiss**, with an optional note.
  - Expanding a row shows each reporter's details.
  - Batch resolve or dismiss.
- **Admin dashboard and nav**: a Problems card with the open count, next to the existing Reports card.
- **Icons** (all already in `partials/lucide_icon.html`): `triangle-alert` for the report buttons, the Problems page and the dashboard card; `refresh-cw` for "File updated since report"; `circle-x` for "Comic removed"; `trash-2` for withdraw; `book-open` for open in reader; `circle-check` for resolve.

## Notifications

Problem reports add no badge or "seen" tracking of their own. Notifications belong to the [Notification Center](notification-center-scope.md).

- **Without the notification center**, the reporter sees the outcome where they already look: the dot and modal on the comic page and in the reader, and the My Reports section.
- **With the notification center**, resolving or dismissing a report also sends `problem_resolved` to the reporter, and a new report sends `admin_problem_new` to admins. New reports can also go to the notification center's outgoing webhook when that is built.

Problem reports do not depend on the notification center, and can ship before or after it.

## Rollout

### Phase 1: Reports and Queue

- Model, migration, `can_report`, `features` settings category and `features.problem_reports_enabled`
- Shared comic access helper
- Report modal on the comic page and in the reader
- Admin Problems page with resolve, dismiss and reopen
- Scanner hook for `file_changed_at`, and keeping reports when comics are deleted
- My Reports section
- `problem_resolved` and `admin_problem_new` notifications, if the notification center has landed

### Phase 2: Polish

- New reports sent to the notification center's outgoing webhook
- "Show page" thumbnails in the admin queue, so the admin can see the reported page without opening the reader
- Corrupt files from the existing Reports section shown on the Problems page as system-found problems, so admins have one list of files to fix

## Testing

- Service tests: create, update-instead-of-duplicate, withdraw, resolve, dismiss, reopen, the access check (library and age rating), the scanner hook, and comic deletion keeping the report.
- API tests: setting off returns 404 for reader endpoints, `can_report` false is refused, readers cannot see others' reports, admin-only endpoints refuse readers.
- `tests/services/test_migrations_match_models.py` keeps passing with the new model and migration.
- Playwright: report from the reader with the page prefilled; report from the comic page; admin resolves with a note and the reporter sees it; buttons hidden when the setting is off.

## Open Questions

| Question | Recommendation |
| --- | --- |
| Default on or off for existing installs? | On. Reports are low risk and do nothing until a reader uses them. Requests default to off because they create admin work by design. |
| Should a reader be able to report a comic with no page (for example "won't open")? | Yes. Page is always optional. |
| Should admins be able to limit how many open reports a user has? | Not in phase 1. One open report per comic and category, plus `can_report`, is enough for household servers. |
| Should resolving a `page_order` or `metadata` report link to the cover or metadata tools? | Later. Phase 1 links to the comic page, where those tools already are. |
