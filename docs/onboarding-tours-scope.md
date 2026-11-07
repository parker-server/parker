# Onboarding Tours and Spotlights Scope

## Status

Proposed. Nothing is built yet. Driver.js is approved as the one new front-end dependency, pinned at **1.10.2** and loaded only on pages that can run a tour or spotlight. The open questions are settled; see [Decisions](#decisions) at the end.

## Goal

Help people find their way around Parker without reading documentation, in two ways:

- **Guided tours.** A short, step-by-step walkthrough with Next, Previous and X. The first one is a welcome tour that points out the main navigation the first time a reader signs in. The same engine can run a page-specific tour later, such as one for admins on the Admin page.
- **Spotlights.** A single highlight on one element, such as "this button is new". Spotlights are driven by What's New entries, so a release that adds a button can point at it without any new code.

Both must work well on phones and must be defined as data, so adding a tour or spotlight means editing a JSON file and adding `data-tour` attributes, not writing JavaScript.

## Product Principle

- Short. A tour has at most about six steps. Every step is skippable and X always ends the tour.
- Never start twice. A tour starts automatically once. After that it runs only when the user asks for it.
- Never get in the way. No tours or spotlights in the reader. Nothing starts while another tour, a modal or the mobile menu (unless the tour opened it) is open.
- Remembered per user, not per device. Finishing the tour on a laptop means it does not start again on a phone.
- A step that cannot be shown is skipped, never shown pointing at nothing.

## Non-Goals

- No tour editor in the admin UI. Tours live in a JSON file in the repo.
- No analytics on how far people get through a tour.
- No conditional branching inside a tour.
- No tours inside the reader or OPDS.
- No Driver.js "checklist" module in this scope.

## Driver.js

Driver.js 1.10.2 (MIT, no dependencies, published 2026-10-18) ships three browser builds. We use two:

| Module | Files | Global | Used for |
|---|---|---|---|
| Tours | `dist/driver.js.iife.js` (~28 KB), `dist/driver.css` | `window.driver.js.driver` | Welcome and page tours |
| Hints | `dist/hints.iife.js` (~17 KB), `dist/hints.css` | `window.driverHints.hints` | Spotlights: a pulsing beacon that opens a popover with one button |

Options in 1.10.x that cover problems we would otherwise solve ourselves:

- `skipMissingElement`: skip a step whose element is not on the page, such as an admin-only link or a desktop-only control.
- `waitForElement` (ms): wait for elements Alpine renders after page load, such as the Libraries dropdown.
- `disableActiveInteraction`: stop a tap on the highlighted link from leaving the page mid-tour.
- `onHighlightStarted`, `onNextClick`, `onDestroyed` and the other hooks: open the mobile menu before a nav step, and save progress when the tour ends.
- `onDestroyStarted` and `onDestroyed` receive a `reason` (`done`, `close`, `escape`, `overlay`): tells a completed tour apart from a dismissed one.
- `popoverClass` and `onPopoverRender`: dark theme styling and accessibility fixes.

Load from jsdelivr with an exact version, the same way Alpine is loaded in `base.html`:

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/driver.js@1.10.2/dist/driver.css">
<script defer src="https://cdn.jsdelivr.net/npm/driver.js@1.10.2/dist/driver.js.iife.js"></script>
```

## Concepts

- **Tour**: an ordered list of steps that runs on one page. It has an `id`, a `page` (route name), an `audience` (`all` or `admin`) and a `trigger` (`auto` for once on first visit, or `manual` for only when asked).
- **Step**: a `target` (a `data-tour` name, or none for a centred intro step), `title`, `body`, and optional `side`, `align`, `mobile_target` and `before`.
- **Spotlight**: one target on one page with a title and body, shown as a beacon. It comes from a What's New entry.
- **Onboarding state**: one row per user per tour or spotlight they have seen, in the `user_onboarding` table.

## Data

### `app/data/tours.json`

Same style as `whats_new.json`: a list of plain objects, validated on load, with invalid entries skipped and logged.

```json
[
  {
    "id": "welcome",
    "page": "home",
    "audience": "all",
    "trigger": "auto",
    "steps": [
      {
        "title": "Welcome to Parker",
        "body": "Here's a quick look at where things are. You can leave at any time and take the tour again from Account Settings."
      },
      {
        "target": "nav-search",
        "mobile_target": "nav-search-mobile",
        "before": "open-mobile-menu",
        "title": "Search",
        "body": "Find series, issues, creators and characters.",
        "side": "bottom"
      },
      {
        "target": "nav-libraries",
        "mobile_target": "nav-libraries-mobile",
        "before": "open-mobile-menu",
        "title": "Libraries",
        "body": "Browse everything on this server, one library at a time."
      }
    ]
  }
]
```

- `page` is the route name (`request.scope["route"].name`), not a URL, so it keeps working behind a base URL.
- `before` names an action from a small fixed list in the JavaScript (`open-mobile-menu`, `close-mobile-menu`). JSON never holds code.
- Changing a tour enough that people should see it again means giving it a new `id`.

### What's New spotlight

Add one optional field to `whats_new.json` entries:

```json
{
  "version": "0.1.50",
  "audience": "all",
  "title": "Filter Insights by library",
  "body": "Pick a library to see stats for just that part of your collection.",
  "link": "/insights",
  "spotlight": { "page": "insights", "target": "insights-library-filter", "side": "bottom" }
}
```

- The spotlight reuses the entry's title and body, so there is nothing extra to write.
- Its id is `<version>:<target>`.
- `WhatsNewEntry` gains an optional `spotlight` field. Older entries are unaffected.
- Spotlights show only for entries from the **two most recent released versions that have spotlights**. Older ones drop off automatically, worked out from `whats_new.json` alone with no extra data. Versions without spotlights don't count, so a bug-fix release doesn't push a spotlight out early.
- A new user does not get spotlights for features that existed before they joined. See [New users and spotlights](#new-users-and-spotlights).

### `data-tour` attributes

Targets are `data-tour="<name>"` attributes on elements, never CSS classes or `data-testid`. Tailwind classes change with styling, and test ids belong to tests. The desktop and mobile copies of a nav link get separate names (`nav-libraries` and `nav-libraries-mobile`), because the desktop nav is `hidden lg:flex` and the mobile links are in the collapsed menu.

## Data Model

Onboarding state goes in its own table, not a JSON column on `User`. The app writes it, not the user, and it only grows: every spotlight adds an id that is never removed. As a JSON column it would load with the current user on every request. As rows, resetting, marking users on upgrade, and counting who finished a tour are each one simple query.

New model `UserOnboarding` in `app/models/user_onboarding.py`, table `user_onboarding`:

| Column | Type | Notes |
|---|---|---|
| `user_id` | Integer, FK `users.id`, `ondelete="CASCADE"` | Part of the primary key |
| `item_id` | String | Part of the primary key. A tour id (`welcome`) or spotlight id (`0.1.50:insights-library-filter`) |
| `kind` | String | `tour` or `spotlight` |
| `status` | String | See below |
| `updated_at` | DateTime | Set on every write |

- The primary key is `(user_id, item_id)`, so one row per user per item. No row means not seen yet.
- `User` gets `onboarding = relationship("UserOnboarding", cascade="all, delete-orphan")`, matching `reading_progress` and `bookmarks`, so rows are deleted with the user.

Status values:
- Tours: `completed` (reached the end), `dismissed` (closed early), `skipped` (set by the migration). For deciding whether a tour starts automatically, all three mean "seen".
- Spotlights: `dismissed`.

An Alembic migration creates the table. The same migration inserts a `welcome` / `tour` / `skipped` row for every existing user, so an upgrade does not start a tour for people who already know Parker. The release's What's New entry tells them where to take it if they want to.

The same rule applies to any `auto` tour added later: the migration or release that ships it marks existing users in its audience as `skipped`. For example, the admin tour's release marks existing admins `skipped`.

### New users and spotlights

The first time a user's welcome tour is saved as `completed` or `dismissed`, and they had no `welcome` row before, the service also inserts `dismissed` rows for every spotlight they could see at that moment. A new user therefore only sees spotlights for features released after they joined. Everything that existed before is covered by the welcome tour, or simply isn't new to them.

- Replaying the tour later does not do this again, because a `welcome` row already exists. Newer spotlights the user hasn't seen yet are kept.
- Existing users get their `skipped` row from the migration, not through this path, so they still see spotlights for the release that ships tours and later ones.
- Until the welcome tour is finished or closed, no spotlights show for that user.

## Settings

One admin setting, next to `features.notifications_enabled` under Settings > Features:

- `features.onboarding_enabled` (default on). When off, no tours or spotlights load for anyone, and the replay link is hidden.

## API Sketch

Extend `app/api/users.py`, next to `/me/preferences`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/users/me/onboarding?page=<route>` | Tours and spotlights for this page that this user can see, each with its status |
| `PATCH` | `/api/users/me/onboarding` | Body `{"tours": {"welcome": "completed"}}` or `{"spotlights": {"<id>": "dismissed"}}`. Upserts one row per item. Unknown ids return 400 |
| `DELETE` | `/api/users/me/onboarding` | Delete all of this user's rows, for "Show tips again" |

Loading and filtering (audience, page, recent versions, state) and reading and writing `user_onboarding` rows live in a small `app/services/onboarding_service.py`, alongside `load_whats_new_entries`. Request and response models go in `app/schemas`.

## Front End

### Loading only where needed

- A Jinja global `page_has_onboarding(request)` returns true when `tours.json` or a recent spotlight names the current route, and the feature is enabled.
- `base.html` includes `partials/onboarding_assets.html` only when it is true. That partial adds the Driver.js tags, the hints tags if the page has a spotlight, and `static/js/onboarding.js`.
- The decision is made per page, not per user, because templates do not know the signed-in user. Users who have already seen the tour download the cached ~28 KB file but nothing runs.

### `static/js/onboarding.js`

A small runner, following the IIFE-on-`window.parker` style used in `storage.js`:

1. On load, fetch `/api/users/me/onboarding?page=<route>`.
2. If an `auto` tour is unseen, build Driver.js steps from it and start it. `?tour=<id>` in the URL starts a tour by id, which is how the replay link works.
3. Otherwise, show any undismissed spotlights with `hints()`.
4. Each step's element is resolved at run time: `mobile_target` below the `lg` breakpoint (1024px, matching the nav), `target` above it.
5. `before` actions run from `onHighlightStarted`. `open-mobile-menu` dispatches a window event that the body's `x-data` listens for and uses to set `mobileMenuOpen = true`. The menu is closed again when the tour ends.
6. `onDestroyed` saves `completed` or `dismissed` from the destroy reason.

### Mobile

- Below 640px, `.parker-tour` popovers become a bottom sheet: fixed to the bottom of the screen, full width with a 16px gutter, and `padding-bottom: env(safe-area-inset-bottom)`. The highlighted element stays visible above it. Driver.js positions popovers with inline styles, so the override uses `!important`. This must be checked in the spike before anything else is built.
- Next, Previous and X are at least 44px tall.
- Smooth scrolling brings each target into view. The sticky nav (`z-[100]`) sits below Driver's overlay.

### Styling and accessibility

- A `.parker-tour` theme in `static/css/style.css` matches the dark gray and blue palette.
- Check in the spike that the popover gets focus, Esc closes it, and arrow keys move between steps (Driver's `allowKeyboardControl`). If it lacks `role="dialog"` and a label, add them in `onPopoverRender`.

### Replay

Account Settings (`/user/settings`) gets a "Take the tour" link to `/?tour=welcome`, and a "Show tips again" button that calls `DELETE /api/users/me/onboarding`. There is no user menu in the nav today, so Settings is the natural home.

## Rollout

### Phase 0: Spike (half a day)

Load Driver.js 1.10.2 on the home page by hand with two steps, then check:

- The bottom-sheet override at 375px.
- The `open-mobile-menu` action.
- `waitForElement` on the Alpine-rendered Libraries dropdown.
- Keyboard and focus behaviour.

If the bottom sheet cannot be made reliable, fall back to Driver's default popover with `side: "bottom"` on phones.

### Phase 1: Foundation and welcome tour

- `user_onboarding` table, model and migration (including `skipped` rows for existing users), service, API, and setting.
- `tours.json` with the welcome tour, `data-tour` attributes in `base.html`, `onboarding.js`, assets partial and theme.
- Replay and reset in Account Settings.
- README section, What's New entry and release notes.

### Phase 2: Spotlights

- `spotlight` field on What's New entries, the hints module, and the version cutoff.
- The first real spotlight ships with whichever release next adds a visible control.

### Phase 3 (optional): Admin tour

- An `auto` tour on `/admin` covering library roots, scanning, Settings and Users, with `audience: "admin"`. It starts the first time an admin opens the Admin page, never on the home page, so it doesn't stack on top of the welcome tour.
- A "Take the admin tour" link on the Admin page replays it with `?tour=admin`.
- Its release adds a migration marking existing admins `skipped` for this tour.

## Testing

- **Service:**
  - `tours.json` loads and validates. Invalid tours and steps are skipped. Filtering by page, audience and state works.
  - Spotlight cutoff: only the two most recent released versions with spotlights are returned. Versions without spotlights don't count, and unreleased versions are never returned.
  - New users: the first `completed` or `dismissed` welcome save dismisses every current spotlight. A later replay does not. A user with a migration `skipped` row still gets spotlights.
  - No spotlights are returned while the user has no `welcome` row.
- **Consistency:** every `target` and `mobile_target` in `tours.json`, and every What's New `spotlight.target`, matches a `data-tour` attribute in `app/templates`. Every `page` is a real route name. This is modelled on `test_shipped_whats_new_settings_links_point_at_real_targets`.
- **API:** GET filters correctly for readers and admins. PATCH saves, updates an existing row instead of adding a second one, and rejects unknown ids. DELETE removes only the current user's rows. The feature setting turns everything off.
- **Model:** deleting a user deletes their `user_onboarding` rows. The migration marks existing users' welcome tour `skipped` and leaves new users with no rows.
- **Browser (Playwright):**
  - Desktop: the welcome tour starts on first visit. Next and Previous move between steps. X dismisses, and the tour does not return after a reload.
  - Mobile (`mobile_page`): the nav step opens the menu, and the popover is inside the viewport.
  - A spotlight beacon appears. Dismissing it is saved.
  - `?tour=welcome` replays a completed tour.
- **Existing browser tests:** the seeded test users must start with a `welcome` row marked completed. Otherwise the auto-started tour overlay would block clicks in every existing browser test. Do this in the browser test seed, not by turning the feature off, so the new tests run against real defaults.

## Decisions

- **Upgrades:** existing users are marked `skipped` for the welcome tour by the migration. Only new users see it automatically. The release's What's New entry points to the replay link in Account Settings.
- **Spotlight lifetime:** a spotlight shows until dismissed, but only while its entry is in one of the two most recent released versions that have spotlights.
- **Admin tour:** starts automatically the first time an admin opens `/admin`, and can be replayed from a link there. Existing admins are marked `skipped` when it ships.
- **New users and spotlights:** a new user never sees spotlights for features that existed before they joined. Finishing or closing the welcome tour for the first time dismisses every spotlight current at that moment. Spotlights wait until then.
