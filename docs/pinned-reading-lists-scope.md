# Pinned Reading Lists Scope

Status: Implemented (MVP)

This note scopes letting a reader pin reading lists to Home, where each pinned list shows up as one card that opens at their next unread issue and shows how far through the list they are.

## Why This Exists

A user asked to add a reading list to one of their Stacks. Looking closer, what they wanted was a way to keep a reading list close at hand and pick it back up where they left off, which Stacks don't do well:

- Stacks are flat, hand-curated lists of single issues. They're meant for loose "mix and match" picks, not for copying a 100+ issue event into.
- Copying a reading list into a Stack would freeze it. When an admin refreshes a CBL list, the Stack wouldn't change.
- Reading lists already have their own order, page, and Continue button. They only lack a way to show up on Home.

Pinning keeps reading lists as they are and gives them a place on Home.

## Product Principle

A pinned reading list is a bookmark to "where I am in this list", not a copy of it.

- One card per pinned list, all in **one** Home rail, so pinning many lists never adds many rails.
- The card always reflects the list as it is now, including CBL refreshes.
- Pins are private to the user, like library pins.

## Current System Shape

Touch points:

- `app/models/reading_list.py`: `ReadingList`, `ReadingListItem` (ordered by `position`). Reading lists are global and admin-managed. Sources are `manual`, `comicinfo` and `cbl`.
- `app/models/interactions.py`: `UserLibraryPin` is the pattern to copy (`user_id` + target id as composite PK, `pinned_at` for order).
- `app/api/libraries.py`: `POST`/`DELETE /{library_id}/pin` are idempotent pin/unpin endpoints. The detail payload includes `pinned`.
- `app/api/reading_lists.py`: list and detail endpoints, including the visibility rules this feature has to match.
- `app/core/comic_helpers.py`: `get_container_resume_target()` picks the Continue issue for an ordered list of comic ids.
- `app/api/home.py`: `HOME_RAIL_DEFINITIONS`, the per-user `home_rail_layout`, and `/pinned-libraries` (one layout slot that holds several rails).
- `app/templates/index.html`: rail rendering and a copy of the default rail list (about line 509), plus the key-to-loader maps.
- `app/templates/reading_lists/reading_list_detail.html`: the header with the Continue / Start Reading button, and `startReading()`, which opens the reader with `context_type=reading_list&context_id=<id>`.

Behavior to keep in mind:

- CBL refreshes (`ReadingListService.sync_cbl_list`) keep the same `ReadingList` row and replace its items, so a pin survives a refresh.
- `comicinfo` lists are deleted by `cleanup_empty_lists()` when they become empty, and could come back later with a new id. A pin on such a list goes away with it.
- Deleting a CBL source deletes its reading list.
- SQLite foreign key enforcement isn't turned on anywhere in `app/`, so `ondelete="CASCADE"` alone won't clean up pins. See Data Model.

## MVP

1. A **Pin to Home** toggle on the reading list detail page, next to the Continue button.
2. A new Home rail, **Pinned Reading Lists**, with one card per pinned list.
3. Each card shows:
   - the cover of the next issue (the Continue target)
   - the list name
   - the next issue as `Series #N`
   - a progress bar with `completed / total` (for example `12 / 48`)
4. Clicking the cover opens the reader at that issue with the reading list context, the same as Continue on the list page. Clicking the name opens the list page.
5. Users can move or hide the rail in the existing Home layout editor, like any other rail.

## Behavior Details

### Next issue and progress

- "Total" is the number of issues in the list that this user can see, using the same filters as the detail endpoint.
- "Completed" is how many of those issues have `ReadingProgress.completed` for this user.
- The next issue comes from `get_container_resume_target()`, so the card and the list page's Continue button always agree.
- `get_container_resume_target()` returns the first issue with status `new` both for an untouched list and a finished one. The rail tells them apart by checking `completed == total`.

### Finished lists

A finished list **leaves the rail right away but stays pinned**. If a CBL refresh later adds issues the user hasn't read, the card comes back by itself. To get rid of it for good, the user unpins it on the list page.

### Order of cards

Recommendation: lists the user read most recently come first (latest `last_read_at` across the list's issues), then lists they haven't started, ordered by `pinned_at`. That keeps the list they're actively reading at the front.

### Visibility

A pinned list only shows up if the user can currently open it:

- It's skipped if it fails the age-restriction check (the same rule as `check_container_restriction` / `get_banned_comic_condition`).
- Only issues in libraries the user can access and that have `parse_reading_lists` turned on count.
- It's skipped if no issues are left after filtering.

Pins on lists the user can't see are kept quietly, not deleted, so they come back if access changes.

### No pin limit

There's no cap on pins. Because everything goes into one rail, many pins only make that rail longer. The endpoint can still return at most around 24 cards so Home stays fast.

## Data Model

New model in `app/models/interactions.py`, mirroring `UserLibraryPin`:

```python
class UserReadingListPin(Base):
    __tablename__ = "user_reading_list_pins"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    reading_list_id = Column(Integer, ForeignKey("reading_lists.id", ondelete="CASCADE"), primary_key=True)
    pinned_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
```

- Add an Alembic migration that creates the table, modeled on `3a6f4d2b9c10_add_user_library_pins_table.py`.
- Because FK cascades aren't enforced, give `ReadingList` a `pins` relationship with `cascade="all, delete-orphan"`. That covers every place a list is deleted with `db.delete(reading_list)`: the admin delete endpoint, CBL source delete, and `cleanup_empty_lists()`. The Home query should also inner-join `ReadingList` so a leftover pin can never break the rail.

## API

On the reading lists router, mirroring library pins:

- `POST /{list_id}/pin` returns `{"reading_list_id": id, "pinned": true}` (idempotent).
- `DELETE /{list_id}/pin` returns `{"reading_list_id": id, "pinned": false}` (idempotent).
- `ReadingListDetailResponse` gains `pinned: bool`.
- Pinning a list the user can't open fails the same way the detail endpoint does: 404 for a missing list or one with no visible issues, 403 for age-restricted content.
- Unpinning always works, even when the list is no longer visible, so an old pin can always be cleared.

On the Home router:

- `GET /pinned-reading-lists` returns a list of:

```json
{
  "reading_list_id": 12,
  "name": "Civil War",
  "total": 48,
  "completed": 12,
  "last_read_at": "2026-11-01T20:14:00Z",
  "next": {
    "comic_id": 3401,
    "series": "Fantastic Four",
    "number": "538",
    "thumbnail_path": "/api/comics/3401/thumbnail?v=...",
    "status": "continue"
  }
}
```

Query plan: load the pinned lists, load all their visible items in one query ordered by `(reading_list_id, position)`, load the user's progress for those comic ids in one query, then work out next/total/completed in Python. That's a fixed number of queries no matter how many lists are pinned. Benchmark on a scratch copy of the server DB before shipping.

## Home Layout

- Add `{"key": "pinned_reading_lists", "title": "Pinned Reading Lists", "hint": "Managed by reading list pins."}` to `HOME_RAIL_DEFINITIONS`, right after `up_next`. `_insert_missing_home_rail_entries()` already adds new keys to saved layouts, so no layout version bump is needed.
- Update the copy of the default rail list in `index.html`, the key-to-loader and key-to-has-content maps, and add the rail template.
- Hide the rail when it has no cards, like the other rails.
- The rail's action link, "Browse Reading Lists", goes to the reading lists page.

## UI

- **Reading list detail page:** a pin toggle button next to the Continue button group (lucide `pin` / `pin-off`), with a toast on change.
- **Home card:** about the width of a `comic_card`. Cover on top, then the list name, a muted `Next: Series #N` line, and a thin progress bar with `12 / 48`. Add a new partial `partials/reading_list_progress_card.html`, reusing the progress bar styling from `partials/progress_card.html`.
- **Reading lists index:** a small pin badge on `reading_list_card.html` for lists the user has pinned. A pinned-only filter waits for later (see Future Options).

## Non-Goals

- Adding reading lists, volumes or series to Stacks. Stacks stay single-issue and hand-picked.
- Pinning story arcs, collections or Stacks. Possible later with the same card, see Future Options.
- Sharing pins, or admin-set "featured" reading lists. Promoted rails already cover admin curation.
- Notifications when a pinned CBL list gains issues.

## Future Options

- Pin story arcs and Stacks using the same card and endpoint shape (`kind` + `id`).
- A "New issues added" badge on a card after a CBL refresh adds issues the user hasn't read.
- A pinned-lists filter on the reading lists index page.

## Implementation Sequence

1. Model, migration and `ReadingList.pins` cascade.
2. Pin/unpin endpoints and `pinned` on the detail response.
3. `GET /home/pinned-reading-lists`.
4. Home layout definition and rail template.
5. Pin toggle on the reading list detail page.
6. README, What's New entry and release notes.

## Validation Targets

API (`tests/api/test_reading_lists.py`, `tests/api/test_home.py`):

- Pin and unpin are idempotent, and `pinned` shows on the detail response.
- Pinning a list the user can't see returns 404.
- The rail returns the right `next`, `total` and `completed` for untouched, in-progress and finished lists.
- Finished lists are left out. A finished CBL list comes back after a refresh adds an issue.
- Age-restricted lists, lists with no accessible issues, and lists from libraries with `parse_reading_lists` off are left out.
- Deleting a reading list (admin delete, CBL source delete, empty comicinfo cleanup) removes its pins.
- Cards are ordered by recent activity, then by `pinned_at`.
- The new rail key is added to existing saved layouts.

Browser (`tests/browser/test_home_customization.py` or a new reading list flow test):

- Pin a list from its page, see the card on Home, and click the cover to open the reader at the expected issue with reading list context.
- Unpin, and the card is gone.
- The rail can be hidden and moved in the layout editor.

## Decisions

- Finished lists leave the rail right away, with no lingering "Finished" state. They come back on their own if a CBL refresh adds unread issues.
- The reading lists index page gets a pin badge in the MVP. A pinned-only filter comes later.
- The rail is named "Pinned Reading Lists", to match "Pinned Libraries".
