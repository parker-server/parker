# Notification Center Scope

## Status

Phase 1 is split into two pull requests. The first (notification center and What's New) is built on `feature/notifications`. The second (follow arrivals and Year in Review) is not started. Open questions are listed at the end.

## Goal

Give Parker one place to tell a user that something happened that they would want to know about, without them having to go looking. Today Parker has no notification system. Features that need one ([Problem Reports](problem-reports-scope.md), [Comic Requests](comic-requests-scope.md)) would each otherwise add their own badge and "seen" tracking.

The first uses are:

- **New issues in followed volumes.** Parker already knows about these (`GET /api/home/following-arrivals`), but only shows them in a home rail the user has to scroll to.
- **What's new in Parker.** New features reach readers only if they happen to notice them. The GitHub release notes are written for admins and developers, and most readers never see them.
- **Year in Review.** Today it is announced only by a banner on the user dashboard in December and January, which readers who go straight to their libraries never see.
- **Updates to things the user asked for.** A problem report was resolved, or a requested comic arrived.
- **Admin-only items.** A new problem report or request is waiting, or a Parker update is available.

## Product Principle

- A notification points at something. It never holds the only copy of information; the linked page is the source of truth.
- Features create notifications through one service call. They never write the table directly, and the notification center knows nothing about any feature.
- A notification must never reveal content the user cannot see, whether hidden by library access or by age rating. Each producer checks this before notifying.
- Quiet by default. Related events are combined into one notification, and every kind can be turned off by the user.

## Non-Goals

- No email, browser push or mobile push in phase 1.
- No live updates (WebSockets or server-sent events). The unread count is fetched on page load and when the tab regains focus, the same way the scanner status in `base.html` checks once on load.
- No user-to-user messages or comments.
- No new frameworks or dependencies.

## Concepts

- **Notification**: one row for one user, with a title, an optional short body, an optional link, and a read time.
- **Kind**: what produced it, such as `follow_arrivals` or `whats_new`. Each kind is declared once in a registry, which sets its label, icon, audience, default and wording.
- **Group key**: lets a producer update an existing unread notification instead of adding another. Ten new issues of one volume arriving over three scans are one notification that says "10 new issues", not ten.
- **Producer**: a function that decides who should hear about something and what data the message needs, then calls `notify()`.
- **Trigger**: the moment a producer runs: after a scan, in the daily check, at startup, or called directly by a feature.

## Kinds

| Kind | Audience | Produced when | Example | Icon | Phase |
| --- | --- | --- | --- | --- | --- |
| `follow_arrivals` | Readers | A scan adds plain issues to a volume the user follows | "3 new issues in Saga (2012)" | `book-open` | 1 |
| `whats_new` | All users (admin entries for admins only) | Parker starts on a version with new What's New entries | "New in Parker 0.1.50: report problems with a comic" | `sparkles` | 1 |
| `year_in_review` | Users who finished a comic this year | Mid-December, once per year | "Your 2026 Year in Review is ready" | `party-popper` (as the dashboard banner) | 1 |
| `problem_resolved` | Reporter | An admin resolves or dismisses their report | "Your report on Saga #12 was resolved" | `circle-check` | 2 |
| `request_fulfilled` | Supporters | A requested comic arrives and the supporter can see it | "Saga #67 is now in the library" | `library` | 2 |
| `admin_problem_new` | Admins | A new problem report is created | "New problem report: missing pages in Saga #12" | `triangle-alert` | 2 |
| `admin_request_new` | Admins | A new request target is created | "New request: Saga #67 (2 supporters)" | `clipboard-list` | 2 |
| `admin_update_available` | Admins | The version check finds a newer release not yet notified | "Parker 0.1.51 is available" | `download` | 2 |

Every icon above already exists in `partials/lucide_icon.html`. A `server_announcement` kind (see [Open Questions](#open-questions)) would add `megaphone`.

Phase 2 kinds arrive with the features that produce them. See [Adding a Kind](#adding-a-kind).

## Architecture

The core is small and fixed. Each kind of change has one place to plug in, so adding a notification means writing one kind definition and one producer, with no changes to the service, the bell, the API or the preferences page.

```
 Triggers            Producers                Core                     Channels
 ────────            ─────────                ────                     ────────
 after scan  ──▶  follow_arrivals  ─┐
 daily check ──▶  year_in_review   ─┤
 startup     ──▶  whats_new        ─┼──▶  NotificationService  ──▶  in-app (table) ──▶ bell, page, API
 direct call ──▶  problem_resolved ─┘     .notify(kind, users,      webhook (phase 3)
                                           data, group_key)
                                               ▲
                                          Kind registry
                                   (label, icon, audience, defaults,
                                    render, link)
```

| Part | Owns | Knows nothing about |
| --- | --- | --- |
| Kind registry | What each kind is called, looks like and says | Who receives it, when it fires |
| Producers | Who should hear about something, the access check, the data for the message, avoiding duplicates | The table, grouping, preferences |
| Triggers | When producers run | What producers do |
| `NotificationService` | The feature setting, preferences, audience, grouping, rendering, writing rows | Any feature |
| Bell, page, API | Showing notifications, using the registry for icons and labels | Any particular kind |

### Kind Registry

Each kind is a frozen dataclass in one dict in `kinds.py`, the same idea as `_TASK_REGISTRY` in the scheduler and the defaults list in `SettingsService`:

```python
@dataclass(frozen=True)
class NotificationKind:
    key: str                     # "follow_arrivals"
    label: str                   # shown in preferences
    description: str             # shown in preferences
    icon: str                    # lucide icon name
    render: Callable[[int, dict[str, Any]], tuple[str, str | None]]  # (count, data) -> (title, body)
    link: Callable[[dict[str, Any]], str | None]
    admin_only: bool = False
    default_enabled: bool = True


KINDS: dict[str, NotificationKind] = {
    kind.key: kind
    for kind in (
        NotificationKind(
            key="follow_arrivals",
            label="New issues in followed volumes",
            description="When a scan adds issues to a volume you follow.",
            icon="book-open",
            render=lambda count, data: (
                f"{count} new issue{'s' if count != 1 else ''} in {data['volume_name']}",
                None,
            ),
            link=lambda data: f"/volumes/{data['volume_id']}",
        ),
        # ...
    )
}
```

- **Producers pass data, not text.** The kind's `render` builds the title and body from `count` and `data`. Grouping is the same for every kind: the core adds to the count, keeps the newest data, and calls `render` again. Wording lives in one place per kind.
- **The bell, page, preferences and API read from the registry.** List responses include each notification's `icon` and `label`, and the preferences endpoint lists the kinds a user can receive. The frontend has no per-kind code.
- **Icons come from the existing macro.** `partials/lucide_icon.html` draws a hand-picked set of Lucide icons in Jinja, and an unknown name draws an empty icon. Because the dropdown is built in the browser from API data, the bell partial renders each registered kind's icon once, server-side, into hidden `<template>` elements keyed by kind, and the Alpine component copies the right one into each row. A kind's icon must exist in the macro (adding one is a few lines of SVG paths); the registry test checks this. The navbar bell uses the macro's `bell` icon.
- **Unknown kinds fail loudly.** `notify()` raises `ValueError` for a key not in `KINDS`, so a typo fails in tests instead of dropping notifications.
- Rows whose kind is later removed from the registry are still shown, with a default icon and their stored text, until retention deletes them.

### Producers and Triggers

A producer is a plain function. It decides who should hear about something, applies the access check (only the producer knows what the content is), and passes the data the kind's `render` and `link` need.

There are four triggers. Three are single hooks holding a list of producers:

| Trigger | Hook | Phase 1 producers | Later |
| --- | --- | --- | --- |
| After scan | One call in `Scanner`, next to the CBL rebuild: `run_scan_producers(db, scan_info)` | Follow arrivals | Request fulfilled |
| Daily | `run_notification_checks(today)`, see [Daily Checks](#daily-checks) | Year in Review, retention | Update available |
| Startup | `run_startup_producers()` | What's New | |
| Direct | The feature calls its producer inline | | Problem resolved, admin new report or request, admin announcements |

- `scan_info` carries what producers need from the scan: library ID, scan start time, whether it was the library's first scan, and the IDs of comics it created.
- A new scan, daily or startup producer is one more entry in that hook's list. The scanner and scheduler are not touched again.
- Each producer runs in its own `try/except`. A failing producer is logged and never fails the scan, the startup or the other producers.
- Calendar producers take `today: date` instead of reading the clock, so tests can pin the date.
- Avoiding duplicates is the producer's job, with two tools: `group_key` merges repeated events while a notification is unread, and a "last done" system setting (`whats_new_last_version`, `year_in_review_last_year`) covers once-only events. The core does not offer "never send this key twice", because retention deletes the rows such a rule would depend on.

Problem Reports and Comic Requests keep their producers in their own service modules and call `notify()` directly. Their logic stays with the feature, and the notification center depends on no feature.

### Notification Service

`NotificationService` is the only code that reads or writes the `notifications` table.

```python
class NotificationService:
    def __init__(self, db: Session) -> None: ...

    def notify(
        self,
        kind: str,
        user_ids: Iterable[int],
        data: dict[str, Any],
        group_key: str | None = None,
        count: int = 1,
    ) -> int: ...
```

`notify()`:

1. Returns 0 when `features.notifications_enabled` is off.
2. Looks up the kind, and raises for an unknown key.
3. Drops non-admins for admin-only kinds, and users who turned the kind off for the in-app channel.
4. With a `group_key`, finds each user's unread notification with that key. If found, it adds `count`, replaces `data`, renders title and body again, and moves `updated_at`. Otherwise it creates a row.
5. Writes in bulk, commits, and returns the number of users notified.

Rules:

- **Best-effort, in its own transaction.** Producers call `notify()` after their feature's work has committed. If notifying fails, it is logged and the feature still succeeds. Resolving a report never fails because a notification could not be written.
- **No content access checks.** Producers pass only users who may see the thing the notification is about.
- The service also owns the read side (unread count, list, mark read, remove) and `cleanup_old_notifications()`, so the API routes stay thin.

### Channels

The table is the source of truth: every notification is in-app first. Other delivery, such as the phase 3 webhook, happens after the row is written. It runs from a small outbox processed by a background job, never inside a scan or a web request, so a slow or failing webhook cannot hold anything up.

Phase 1 builds only the in-app channel and adds no channel interface. Two things keep the later change small:

- Preferences are stored per channel from the start (see [Settings](#settings)), so adding `webhook` needs no data migration.
- All delivery goes through `notify()`, so a second channel is added in one place.

### Adding a Kind

For example, an admin posting a server announcement:

1. Add a `NotificationKind` to `KINDS` with its label, icon, `render` and `link`. If the icon is new, add its paths to `partials/lucide_icon.html`.
2. Write a producer that picks the users and calls `notify()`.
3. Attach it to a trigger. Here, the admin form's endpoint calls it directly.
4. Test the producer. The shared registry test covers the new kind's `render` and `link`.

No migration, API change or frontend code change is needed.

### Layout

```
app/services/notifications/
    __init__.py      # exports NotificationService and the trigger hooks
    kinds.py         # NotificationKind and KINDS
    service.py       # NotificationService: notify, grouping, preferences, reads, retention
    producers.py     # follow arrivals, Year in Review, What's New, and the scan, daily and startup hook lists
```

A small package keeps the registry, the core and the built-in producers apart without spreading them across the codebase. If `producers.py` grows large, it can split into one module per producer later.

### Left Out on Purpose

- **An event bus or publish/subscribe system.** With four triggers, plain function calls are easier to follow and debug.
- **Plugin discovery or loading kinds at runtime.** A dict in `kinds.py` is enough.
- **Notification text in the database or editable by admins.** Wording lives in code, next to `render`.
- **Real-time delivery.** Page load plus tab focus covers how Parker is used, and could be added later without changing producers.

## Producers

### Follow Arrivals

- **When**: after a library scan, next to the existing CBL rebuild loop in `Scanner` (`app/services/scanner.py`). A failure is logged and never fails the scan.
- **What**: plain issues (`get_format_filters()`) created since the scan started, in volumes that have followers. This starts from `UserVolumeFollow` rows (few), not from all comics, so it stays cheap on the 73k-comic server database.
- **Access**: each follower is notified only about comics they can see, using the same library and age-rating filters as `get_following_arrivals()` in `app/api/home.py`. That filter logic should be shared rather than copied.
- **Grouping**: one notification per user per volume, group key `follow:volume:{volume_id}`. While it is unread, later arrivals update its count, title and timestamp. Once read, the next arrival starts a new one.
- **Link**: the volume page.
- **Bulk imports**: a large batch of comics appearing at once is a backfill, not "new issues" a follower is waiting for. The producer skips:
  - the first scan of a library (`Library.last_scanned` is null when the scan starts)
  - any scan that adds more than 500 comics to followed volumes in total (a constant in the service, not a setting)

  Skipped batches are logged with their size. Kavita migration and library relocation do not create comic rows, so they need no rule of their own. Grouping also limits the damage if a large batch gets through: each volume still produces only one notification per user.

### What's New

Release notes stay as they are (`docs/releases/x.x.x.md` with the four required sections). Reader-facing highlights live in a separate data file, written in plain language and kept short:

`app/data/whats_new.json`

```json
[
  {
    "version": "0.1.50",
    "audience": "all",
    "title": "Report a problem with a comic",
    "body": "Spotted missing pages or a bad scan? Use the flag in the reader to let your admin know.",
    "link": "/user/notifications"
  },
  {
    "version": "0.1.50",
    "audience": "admin",
    "title": "Problem reports",
    "body": "Readers can now report problems. Review them under Admin > Problems, or turn the feature off in Settings > Features.",
    "link": "/admin/problems"
  }
]
```

- **When**: at startup. Parker stores the last version it announced in a system setting (`notifications.whats_new_last_version`). Entries newer than that, and not newer than the running version, are announced, then the setting is updated.
- **Who**: users who exist at that moment. New users do not get a backlog of old announcements.
- **Grouping**: one notification per user per upgrade, even when several versions are skipped: "What's new in Parker 0.1.50" with the entry count. The producer passes `data = {"version": ..., "entries": n}` and the kind's `render` writes the text. It links to a **What's New** page (`/whats-new`) that lists all entries for the user's audience, newest first.
- **First start of the notification center**: the setting starts empty. On a fresh install (no libraries yet) nothing is announced. On a server that already has libraries, the running version's entries are announced, so the release that adds notifications can introduce itself. Either way the setting is then set to the running version.
- When notifications are off, the version is still recorded, so turning them on later does not announce old news.
- An entry is optional per release. Most bug-fix releases will have none.

### Year in Review

The Year in Review page (`/user/year-in-review?year=YYYY`) and its dashboard banner already exist. The banner shows in December and January, for the year just ending, to users who read something that year, and is dismissed per browser. The notification reaches users who never open the dashboard.

- **When**: the daily notification check (see [Daily Checks](#daily-checks)) sends it on the first run on or after December 15. If the server is off over the holidays, a run in January still sends it for the previous year, matching the banner's window. After January 31 it is not sent.
- **Once per year**: a system setting (`notifications.year_in_review_last_year`) records the last year sent, so restarts and repeat runs do nothing.
- **Who**: active users who completed at least one comic that year, the same rule the banner uses (`issues_read > 0`), found with one grouped query over `ReadingProgress`. Users with no reading get nothing.
- **Access**: the review covers the user's own reading and the page already applies their filters, so the producer needs no extra check.
- **Link**: `/user/year-in-review?year={year}`.
- **Copy** (the kind's `render`, with `data = {"year": year}`): "Your {year} Year in Review is ready", body "See your favorite series and reading milestones from this year."
- The dashboard banner stays as it is.

### Admin Kinds (phase 2)

Problem reports and requests call `notify()` for all admins when they are created, with group keys (`admin:problems`, `admin:requests`) so a busy day is one notification with a count. `admin_update_available` is created by the version check in `app/services/version_check.py` when it finds a version newer than the last one it notified about.

## Data Model

New model in `app/models/notification.py`, plus an Alembic migration.

`notifications`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer PK | |
| `user_id` | FK `users.id`, `CASCADE` | |
| `kind` | String | One of the kind keys |
| `title` | String | From the kind's `render`, stored when created or grouped |
| `body` | Text, nullable | From the kind's `render` |
| `link` | String, nullable | From the kind's `link`. App-relative path such as `/volumes/12`; the base path is added when rendering, as `url()` does in templates |
| `group_key` | String, nullable | See [Concepts](#concepts) |
| `count` | Integer, default 1 | Items combined into this notification |
| `data` | JSON | The producer's newest data, kept so a grouped notification can be rendered again |
| `created_at` / `updated_at` | DateTime | `updated_at` moves when a grouped notification gets a new item, and is the sort order |
| `read_at` | DateTime, nullable, indexed with `user_id` | |

Indexes: (`user_id`, `read_at`) for the unread count, (`user_id`, `group_key`) for grouping.

Text is stored, and rendered again only when a grouped notification gets a new item. If a series is renamed or a comic is deleted later, old notifications keep their old wording, and the link may lead to a 404 page. This is acceptable for something that expires.

`users`: add `notification_preferences` (JSON, nullable), following the pattern of `home_rail_layout`. It is keyed by kind, then by channel, for example `{"follow_arrivals": {"in_app": false}}`. Missing keys mean the kind's default.

## Settings

In the `features` category proposed in the [Problem Reports scope](problem-reports-scope.md#settings):

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `features.notifications_enabled` | bool | `true` | Turns off the bell, the page and all producers |

System settings:

| Key | Type | Default | Notes |
| --- | --- | --- | --- |
| `notifications.retention_days` | int | `30` | Read notifications older than this are deleted |
| `notifications.whats_new_last_version` | string | Installed version | Internal; not shown on the settings page |
| `notifications.year_in_review_last_year` | int | Empty | Internal; not shown on the settings page |

User preferences, on `/user/settings`: one on/off toggle per kind the user can receive, built from the kind registry. Defaults are on for every kind. The user chose to follow a volume or report a problem, so hearing back is expected. Phase 1 shows one toggle per kind for the in-app channel; the stored shape already allows a toggle per channel later.

## Daily Checks

Some producers are driven by the calendar rather than by a scan. They run in one fixed daily job, `run_notification_checks`, registered with the scheduler in `app/services/scheduler.py` at 9 AM server time and once at startup.

It is not added to `_TASK_REGISTRY`, because registry tasks are admin-tunable (daily, weekly, monthly or disabled), and these checks must run daily to land on the right day. Each check is cheap and does nothing on most days. Turning off `features.notifications_enabled` stops the producers; retention still runs so existing rows are cleaned up.

The job calls the daily hook list described in [Producers and Triggers](#producers-and-triggers), passing the server's local date.

Phase 1 checks:

- Year in Review
- Retention (below)

Phase 2 can move `admin_update_available` here instead of tying it to the version check being opened.

## Retention

`NotificationService.cleanup_old_notifications()` runs from the daily notification check, following the pattern of `cleanup_old_jobs()`. It lives in the notification service, which owns the table, rather than in `MaintenanceService`. The scheduled cleanup task defaults to monthly, which is too slow for a 30-day window.

- delete read notifications older than `notifications.retention_days`
- delete unread notifications older than 90 days, so a user who never logs in does not collect rows forever

## API Sketch

New `app/api/notifications.py`, following the existing router and `SessionDep` patterns. Routes are plain `def`, per `docs/async-route-conversion-scope.md`.

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/notifications/unread-count` | user | Cheap count for the bell |
| `GET` | `/api/notifications` | user | Paginated list, newest first, optional `unread_only`; each item includes its kind's `icon` and `label` |
| `POST` | `/api/notifications/{id}/read` | user | Mark one read (also done when its link is followed) |
| `POST` | `/api/notifications/read-all` | user | Mark all read |
| `DELETE` | `/api/notifications/{id}` | user | Remove one |
| `DELETE` | `/api/notifications` | user | Clear all read notifications |
| `GET` | `/api/notifications/preferences` | user | Kinds this user can receive, with on/off state |
| `PUT` | `/api/notifications/preferences` | user | Save preferences |
| `GET` | `/api/whats-new` | user | What's New entries for the user's audience |

All return 404 when `features.notifications_enabled` is off, except `/api/whats-new`, which stays available.

## UI Surfaces

- **Bell in the navbar** (`app/templates/base.html`), between the scanner status and the search widget, with an unread count. The count is fetched on page load and again on `visibilitychange` when the tab becomes visible. No polling timer.
- **Dropdown** under the bell: the 10 most recent notifications, unread ones highlighted, each with its kind icon (`partials/lucide_icon.html`), title, body and relative time. Clicking one marks it read and follows the link. Footer actions: **Mark all read** and **View all**.
- **Phones**: the bell stays in the top bar at every width, so the mobile menu needs no separate entry. On small screens the dropdown spans the screen width instead of hanging off the bell.
- **Notifications page** (`/user/notifications`): full list with All and Unread tabs, mark read, remove, clear read, and a link to preferences.
- **Preferences**: a Notifications section on `/user/settings`.
- **What's New page** (`/whats-new`): entries grouped by version. Also linked from the admin About page.
- The bell and its Alpine component live in a partial (`partials/notification_bell.html`) so `base.html` only includes it.
- **Icons** (all from `partials/lucide_icon.html`): `bell` for the navbar, `circle-check` for mark read and mark all read, `trash-2` for remove and clear read, `settings` for the preferences link, and each kind's icon from the registry. `check-check` (mark all read) and `bell-off` (empty list, muted kind) would read slightly better but are optional additions.

## Relationship to Other Scopes

- **Problem Reports**: phase 1 of that scope shows report status where the reporter already looks (the comic page and My Reports) and needs nothing from here. Once this lands, resolving a report also produces `problem_resolved`, and new reports produce `admin_problem_new`. The report-specific `seen_at` and badge are no longer needed.
- **Comic Requests**: the "ready" navbar badge and `seen_at` in that scope should be replaced by `request_fulfilled` notifications, and its phase 2 webhook should be the shared one below.
- **Following arrivals home rail**: stays. The rail is the browsing view; the notification is the alert.

## Rollout

### Phase 1: Center, Follows, What's New and Year in Review

- Model, migration, `notification_preferences`, settings
- `app/services/notifications/` package: kind registry, `NotificationService` with grouping and preferences, and the scan, daily and startup hooks
- Bell, dropdown, notifications page, preferences section
- Follow arrivals producer after scans, with the bulk import guard
- `whats_new.json`, startup announcer and What's New page
- Daily notification check with Year in Review and retention
- A release checklist line: add What's New entries for reader-visible features

### Phase 2: Feature Producers

- `problem_resolved` and `admin_problem_new` when Problem Reports lands
- `request_fulfilled` and `admin_request_new` when Comic Requests lands
- `admin_update_available` from the version check

### Phase 3 (Optional): Outgoing Webhook

- One admin webhook setting with Discord-compatible payloads, sent for admin kinds. This is the webhook both other scopes mention, built once here as the second channel, with an outbox processed by a background job (see [Channels](#channels)).
- Email and browser push stay out of scope unless there is a clear need.

## Testing

- Registry test: every kind has a label and description, its icon renders a non-empty SVG through `partials/lucide_icon.html`, and its `render` and `link` work with sample data.
- Service tests: an unknown kind raises; grouping adds to the count, re-renders the title and starts a new notification after the old one is read; preferences and admin-only kinds filter users; the server-wide setting drops calls; a failing `notify()` does not raise into the caller's hook.
- Hook tests: a producer that raises is logged and the other producers in the same hook still run, and the scan still succeeds.
- Follow producer tests: only followers who can see the comic are notified (library and age rating); only comics from this scan; a library's first scan and batches over the limit are skipped; one notification per volume.
- Year in Review tests, with a fixed date: nothing before December 15; sent on December 15 to users with reading that year and not to others; a January run sends for the previous year if December was missed; nothing after January 31; a second run in the same season sends nothing.
- What's New tests: fresh install announces nothing; an upgrade across several versions creates one notification per user; admin entries reach admins only; users created later get none.
- Retention tests, including that cleanup still runs through the daily notification check when notifications are off.
- Test files: `tests/services/test_notification_service.py`, `tests/api/test_notifications.py` and `tests/browser/test_notifications_browser.py`.
- `tests/services/test_migrations_match_models.py` keeps passing.
- Playwright: the bell shows a count after a followed volume gets a new issue; clicking a notification opens the volume and clears the count; mark all read; turning a kind off in preferences stops new ones; the bell is hidden when the setting is off.

## Open Questions

| Question | Recommendation |
| --- | --- |
| What date should the Year in Review notification go out? | December 15. Most of the year's reading is done, it lands before the holidays, and the review page keeps updating until the year ends. The dashboard banner keeps its December 1 start. |
| Write one row per user for What's New, or store announcements once and track what each user has seen? | One row per user. Parker servers have few users, and one table keeps the read path, the bell and retention simple. |
| Should What's New entries be written for every release? | No. Only releases with something a reader would notice. Bug-fix releases usually have none. |
| Should the bell poll while a page stays open? | No in phase 1. Page load plus tab focus covers how Parker is used. The reader page can skip the bell entirely to stay distraction-free. |
| Should admins be able to post their own announcements ("Server down Sunday")? | Useful and cheap on top of this (a `server_announcement` kind sent to all users from an admin form, with a new `megaphone` icon), but phase 2 at the earliest. |
