# Home Rail Customization Scope

Status: Draft

This note captures a possible implementation path for letting each Parker user reorder and hide home page rails.

The goal is to make the home page feel more personal without turning every rail source into a separate settings feature. Parker already has several per-user discovery signals, so the first version should be a small presentation layer on top of the existing rail APIs rather than a rewrite of home discovery.

## Why This Exists

The home page currently has a fixed rail order in `app/templates/index.html`.

That works as a sensible default, but users may value different things:

- a reader who lives in `Jump Back In` and `Up Next` may want those first
- a collector may care more about pinned-library rails and recently updated series
- a social/discovery user may want `Trending`, `Popular with Others`, and Parker ratings higher
- a private or focused user may want to hide social or random rails entirely
- a user with many smart-list and pinned-library rails may need a quieter home page

Parker already lets users opt individual smart lists into the dashboard and pin libraries to Home. This feature would generalize the final home-page presentation order without replacing those source-specific choices.

## Current System Shape

The current home page is assembled client-side:

- `app/templates/index.html` owns the fixed visual order
- the `homePage()` Alpine component fetches each rail independently
- core home rail endpoints live in `app/api/home.py`
- `Recent` and `Updated` come from dedicated home endpoints that rank series by comic add/update timestamps
- smart-list rails are fetched from `/api/smart-lists` and filtered client-side by `show_on_dashboard`
- pinned-library rails come from `/api/home/pinned-libraries` and are already per-user

Current fixed rail groups include:

- `Jump Back In`
- `New from Following`
- `Up Next`
- `Pinned Libraries`
- smart-list rails
- `Want to Read`
- `Critically Acclaimed`
- `Top Rated on Parker`
- `Trending`
- `Popular with Others`
- `Random Gems`
- `Recently Added Series`
- `Recently Updated Series`

Important existing constraints:

- rail item queries must continue enforcing library access and age-rating restrictions
- social rails must continue respecting anonymous social-insights participation rules
- hidden rails should not become a permissions boundary
- empty rails should still disappear from Home
- startup notices and empty-library states should keep taking precedence over the normal home feed
- users may hide every configurable rail, but Home should then show a non-rail fallback with Customize and Reset actions instead of a blank page

## Non-Goals

Out of scope for the first pass:

- changing what each rail returns
- adding admin-enforced global rail policy
- exposing hidden rails as a security/privacy mechanism
- building a generic page-builder system
- changing smart-list query semantics
- changing pinned-library behavior beyond its position in the full home order
- persisting per-device rail preferences separately from account preferences

## Recommended Model

Add a small account-scoped JSON preference to the existing `users` table.

Suggested column:

- `users.home_rail_layout`

Suggested JSON shape:

```json
{
  "version": 1,
  "rails": [
    { "key": "resume", "visible": true },
    { "key": "up_next", "visible": true },
    { "key": "following_arrivals", "visible": false }
  ]
}
```

The order of objects in the `rails` array is the user's chosen rail order. Avoid a separate `position` or `order` property in the MVP because the layout is naturally an ordered list, and drag-and-drop UI can save the whole list after a reorder.

The backend should resolve the saved JSON against Parker's canonical default layout:

- `null` or missing JSON means default order and visibility
- missing built-in rail keys are inserted from the default layout as visible
- removed rail keys are ignored
- unknown keys submitted by a client are rejected
- only stable fields such as `key` and `visible` are persisted

This is a better first fit than a separate table because the data is small, per-user only, rarely written, and not something Parker needs to query across users. A normalized preference table can remain a future escape hatch if the feature grows into per-rail auditing, admin policy, or complex dynamic child-rail customization.

## Rail Keys

Parker should define canonical rail keys in one backend location so the API, template, and tests do not drift.

Candidate fixed keys:

- `resume`
- `following_arrivals`
- `up_next`
- `pinned_libraries`
- `smart_lists`
- `want_to_read`
- `top_rated`
- `top_parker_rated`
- `trending`
- `popular`
- `random_gems`
- `recently_added_series`
- `recently_updated_series`

Dynamic child rails would need stable derived keys if they become individually customizable later:

- pinned library rail: `pinned_library:{library_id}`
- smart-list rail: `smart_list:{smart_list_id}`

These dynamic keys are listed for a possible later version. The MVP should avoid storing them directly unless Parker decides to let users reorder individual pinned-library or smart-list child rails.

Recommended MVP behavior:

- allow ordering and hiding of the top-level `pinned_libraries` group
- allow ordering and hiding of the top-level `smart_lists` group
- preserve each group's internal order initially
- revisit individual dynamic child rail ordering later if real usage calls for it

Why keep dynamic children grouped at first:

- pinned-library order already comes from pin time
- smart-list visibility already exists via `show_on_dashboard`
- dynamic children appear and disappear as users pin libraries or create/delete smart lists
- grouping avoids stale object-specific entries in the saved JSON

## API Shape

Add a small account-scoped home layout API.

Candidate endpoints:

- `GET /api/home/layout`
- `PUT /api/home/layout`
- `POST /api/home/layout/reset`

`GET /api/home/layout` should return the resolved layout, not only the raw JSON stored on the user.

Layout reads should be side-effect-free. If the saved JSON is missing a newly added built-in rail, `GET /api/home/layout` should include that rail as visible in the response without writing it back to `users.home_rail_layout`. The new rail is only persisted after the user saves from the customization UI.

Example response shape:

```json
{
  "rails": [
    {
      "key": "resume",
      "title": "Jump Back In",
      "visible": true,
      "customized": false
    },
    {
      "key": "following_arrivals",
      "title": "New from Following",
      "visible": true,
      "customized": false
    }
  ]
}
```

`PUT /api/home/layout` should accept the user's desired order and visibility for known rail keys.

Recommended request shape:

```json
{
  "rails": [
    { "key": "resume", "visible": true },
    { "key": "up_next", "visible": true },
    { "key": "following_arrivals", "visible": false }
  ]
}
```

The backend should preserve the submitted array order as the user's order. Unknown keys should be rejected so old clients cannot silently persist typoed rails.

`POST /api/home/layout/reset` can clear `users.home_rail_layout` and return the default resolved layout.

## Backend Rendering Approach

The first implementation does not need to combine all rail data into one endpoint.

Recommended MVP:

- Home fetches `GET /api/home/layout` first
- the Alpine component stores the resolved rail order
- each rail keeps its existing fetch function
- the template renders rails through a data-driven order rather than hard-coded page order
- rails marked hidden are not fetched
- empty fetched rails are skipped as they are today
- if no configurable rails are visible, Home shows a simple customization fallback rather than forcing any content rail to stay on

This preserves the current independent loading behavior and avoids turning `/api/home` into a heavy aggregate endpoint.

Possible later optimization:

- add `GET /api/home/feed` that returns the resolved layout plus visible rail payloads in one response
- use that only if independent fetches become too chatty or hard to coordinate

## UI Direction

The customization surface should be easy to find but not in the way during normal reading.

Recommended MVP UI:

- add a small `Customize` action near the home page heading or in the user dashboard
- open a modal or dashboard panel listing home rails
- use drag handles for order
- use toggles for visibility
- include `Reset to Default`
- save explicitly

Avoid exposing this as many disconnected settings. Users should be able to answer one question: "What appears on my Home page, and in what order?"

Consider showing rail status hints:

- `Hidden`
- `Visible`
- `No items right now`
- `Managed by Smart Lists`
- `Managed by Pinned Libraries`

These hints should be explanatory in the settings surface only, not clutter on the normal home page.

## Default Order

The default order should match today's home page order for backwards compatibility:

1. `resume`
2. `following_arrivals`
3. `up_next`
4. `pinned_libraries`
5. `smart_lists`
6. `want_to_read`
7. `top_rated`
8. `top_parker_rated`
9. `trending`
10. `popular`
11. `random_gems`
12. `recently_added_series`
13. `recently_updated_series`

New built-in rails should be visible by default, even for users who have customized their layout. During backend layout resolution, Parker should insert missing built-in rail keys near their default neighbors where practical so newly added rails can be showcased without requiring users to discover them manually in Customize.

No content rail should be permanently "always on" in the MVP. Page-level states such as startup diagnostics, empty-library onboarding, and the no-visible-rails customization fallback are not rails and should remain available when appropriate.

## Migration and Compatibility

Implementation should include an Alembic migration that adds the nullable JSON column to `users`.

No backfill is needed because `null` means default behavior.

Compatibility expectations:

- existing users see the same home page until they customize it
- new built-in rails appear as visible for existing custom layouts without mutating saved JSON on page load
- users can reset by clearing `users.home_rail_layout`
- deleted smart lists and unpinned libraries should naturally stop rendering
- removed built-in rail keys should be ignored during layout resolution
- clients should tolerate new rail keys returned by the layout API if they are only reading the API
- users can hide every configurable rail and still get a recoverable no-visible-rails state

## Testing

Recommended API tests:

- default layout returns today's order
- hidden rails are persisted per user
- submitted array order is persisted per user
- missing built-in rails are inserted as visible during layout resolution
- layout reads do not persist missing built-in rails until the user saves
- one user's layout does not affect another user
- unknown rail keys are rejected
- reset clears the user's JSON layout

Recommended browser tests:

- customize order, reload Home, confirm the new order
- hide a rail, reload Home, confirm it is absent and not fetched if practical to observe
- hide all rails, reload Home, confirm the no-visible-rails fallback offers Customize and Reset
- reset to default restores today's order
- empty rails remain hidden even when visible in the layout

Focused tests around current rail endpoints should remain in place because layout preferences should not weaken existing access or age-rating filtering.

## Open Questions

- Should individual pinned-library rails be reorderable, or is pin order enough?
- Should individual smart-list rails be reorderable here, or should smart-list order live with smart-list management?
- Should hiding `Popular with Others` also imply disabling social-insights participation, or should display and participation remain separate?
- Should admins be able to define a server-wide default order for new users later?
- Should the user dashboard or Home itself be the primary editing surface?

## Recommendation

Start with an account-scoped JSON field on `users` for built-in top-level rail keys.

The first pass should keep existing rail data endpoints, preserve the current default order, and make customization a presentation concern. Only move to a combined home feed endpoint, a separate preference table, or per-dynamic-child ordering if the simple JSON-backed version proves awkward in real use.
