# Shared Stacks Scope

Status: Draft

This note captures a possible first pass for sharing Parker Stacks while keeping the current private-library model, user identity expectations, moderation needs, and age restrictions intact.

## Why This Exists

Stacks are currently private, user-owned collections of comics. Sharing them could make Parker more useful as a curation tool:

- reading orders and event paths
- favorite runs or character spotlights
- recommendations between users on the same Parker instance
- "look at this shelf" links without requiring another user to rebuild the same stack
- future public discovery surfaces if shared stacks prove valuable

The feature is attractive because it adds low-friction curation. The risk is that it also introduces public user identity, public user-generated text, and cross-user visibility rules. The first pass should earn the usefulness without turning Parker into a broad social platform.

## Product Principle

Shared stacks should be private by default, explicit to publish, reversible, and scoped by the viewer's existing access rules.

The first pass should be:

- authenticated
- instance-local
- unlisted by link
- attributed to one stable public username per user
- moderated only where content becomes public
- filtered per viewer

Out of the gate, this should not include public search, comments, likes, follows, ratings, or public user profile pages.

## Current System Shape

The product calls these Stacks, while the backend still uses `PullList` naming.

Important existing touch points:

- `app/models/pull_list.py`
- `app/models/user.py`
- `app/schemas/pull_list.py`
- `app/api/pull_lists.py`
- `app/routers/pages.py`
- `app/templates/pull_lists/index.html`
- `app/templates/pull_lists/detail.html`
- `app/templates/partials/add_pull_list_modal.html`
- `tests/api/test_pull_lists.py`
- `tests/browser/test_saved_search_pull_list_and_session_flows.py`

Current behavior:

- `PullList` has `user_id`, `name`, `description`, `created_at`, and `updated_at`.
- `PullListItem` stores `pull_list_id`, `comic_id`, and `sort_order`.
- Stack list/detail API routes require ownership by `current_user.id`.
- Stack item counts and details already filter age-restricted items in some paths.
- Search and dashboard surfaces treat stacks as private current-user data.
- Stack names are trimmed and limited to 120 characters.
- Stack descriptions are trimmed and limited to 500 characters.

The sharing feature should extend these surfaces rather than introduce a separate list type.

## MVP Recommendation

Start with unlisted shared stacks that can only be viewed by signed-in users on the same Parker instance.

MVP behavior:

- stacks remain private by default
- the owner can share or unshare a stack from the stack detail page
- sharing requires the owner to choose a public username first
- the public username is set once per user and has no edit UI in the first pass
- shared links are stable until the owner unshares the stack
- viewers only see comics their account is allowed to see
- private stack names/descriptions do not need public-content moderation
- stack name, description, and public username must pass public-content validation before sharing
- admins can unpublish a shared stack if needed

Recommended non-goals:

- anonymous public links
- public discovery/search
- public user profile pages
- comments, likes, follows, or social ranking
- per-stack display names
- public username editing
- exact hidden-item counts for viewers
- forking/copying another user's stack

Authenticated-only sharing is intentionally conservative. It lets Parker reuse real user permissions for library access and age restrictions. Anonymous/public web sharing can remain a future decision once there is a clear product need.

## Public Username

Shared stacks should identify their owner with a user-level public username, not a per-stack name.

Recommended fields on `users`:

- `public_username`: nullable, unique, indexed
- `public_username_created_at`: nullable timestamp
- `public_username_moderation_status`: optional, default `approved`

First-pass rules:

- the user chooses the public username before their first share
- Parker must not silently copy email, login username, or real name into this field
- the username is shown consistently on every shared stack by that user
- no edit UI exists in the first pass
- the value is still tied internally to immutable `users.id`
- deleting a user deletes or unpublishes their shared stacks through existing cascades

Suggested validation:

- 3 to 30 characters
- letters, numbers, underscores, and hyphens
- unique case-insensitively
- cannot look like an email address
- cannot start with reserved words such as `admin`, `api`, `auth`, `shared`, `settings`, `users`, or `stacks`
- must pass the same public-content moderation guard as shared stack names

Future username decisions:

- allow edits with a cooldown
- reserve old usernames for a period
- redirect old public profile URLs if profile pages are added
- allow admins to force-rename or revoke abusive usernames

## Sharing Model

Recommended fields on `pull_lists`:

- `share_visibility`: string enum-like value, default `private`
- `share_token`: nullable, unique, indexed
- `shared_at`: nullable timestamp
- `shared_moderation_status`: string enum-like value, default `private`
- `shared_block_reason`: nullable text for admin/debug context
- `shared_disabled_at`: nullable timestamp

Recommended `share_visibility` values:

- `private`
- `unlisted`

Future values:

- `public`
- `followers_only` if Parker ever gains richer social features

Use a non-enumerable token for MVP URLs instead of exposing sequential stack IDs:

```text
/shared/stacks/{share_token}
```

The token should be generated when the stack is first shared and should remain stable across stack renames. Unsharing can either keep the token for later resharing or clear it and generate a new one next time. The safer MVP is to clear the token when unshared so old links stop working immediately.

## Moderation

Only public-facing text needs the stronger moderation path.

Public-facing fields:

- `users.public_username`
- `pull_lists.name` when `share_visibility != "private"`
- `pull_lists.description` when `share_visibility != "private"`

Private stack names and descriptions can keep today's normal validation. When a user shares a stack, or edits an already-shared stack, Parker should validate that the public-facing fields are acceptable.

Recommended first-pass moderation:

- trim and normalize whitespace
- keep existing length limits
- reject obvious profanity, slurs, harassment, and abuse using a local blocked-term list
- reject script/HTML injection by treating user text as text everywhere it renders
- store a moderation status for shared stacks
- let admins unpublish shared stacks

Recommended status values:

- `private`: not shared
- `approved`: shared and visible
- `blocked`: failed validation or manually unpublished
- `needs_review`: future/manual-review escape hatch

For a self-hosted app, a local blocked-term list plus admin unpublish controls is a reasonable MVP. External moderation services should not be introduced unless Parker later supports broader public discovery.

## Age And Access Filtering

Shared stacks must not bypass the viewer's normal permissions.

The shared stack item query should apply:

- stack `share_visibility`
- stack moderation status
- viewer authentication
- viewer library access
- viewer age-rating restrictions
- existing series-level poison-pill behavior where Parker uses it for containers

Viewer behavior:

- show only comics visible to the current viewer
- preserve the owner's stack order for visible items
- do not reveal exact hidden-item counts to viewers
- if some items are hidden, show a neutral note such as `Some items are unavailable for this profile.`
- if zero items are visible, show a neutral unavailable state instead of an item list

The zero-visible case is the most sensitive. Recommended MVP behavior:

- signed-in viewer reaches the shared URL
- Parker confirms the stack exists and is shared
- if no items are visible, show `No stack items are available for this profile.`
- do not show hidden comic titles
- avoid exact counts such as `0 of 17 visible`

Owner behavior:

- the owner can see a share preview
- the owner can see total item count and their own visible item count
- if useful, the owner can see a warning that other users may see fewer items depending on library and age settings

Open age-policy question:

- Should the shared stack title and description still render when zero items are visible to a restricted viewer?

Recommended MVP answer:

- show the stack title and username only after moderation approval
- hide or collapse the description when zero items are visible
- revisit if stack descriptions start being used as content spoilers or policy workarounds

## API And Page Shape

Owner APIs:

- `GET /api/pull-lists/{list_id}/share`
- `POST /api/pull-lists/{list_id}/share`
- `DELETE /api/pull-lists/{list_id}/share`

Public identity APIs:

- `GET /api/users/public-identity`
- `POST /api/users/public-identity`

Shared viewer APIs or page routes:

- `GET /shared/stacks/{share_token}`
- optional `GET /api/shared/stacks/{share_token}` if the page remains Alpine-driven

Admin APIs:

- list shared stacks
- unpublish a stack
- optionally block a public username

The public route should use shared-stack access rules, not the owner-only `PullList.user_id == current_user.id` filter. Existing owner routes should remain owner-only.

## UI Direction

Owner stack detail:

- add a share action near existing stack actions
- if the user has no public username, open a one-time username setup modal
- show clear copy that the username will be public and not editable in the first pass
- after sharing, show copy-link and unshare actions
- show `Shared` status on the stack detail page
- warn that other users may see fewer items

Stack index:

- optionally show a small `Shared` marker on shared stacks
- keep private stacks visually unchanged

Shared stack page:

- show stack title
- show `by {public_username}`
- show visible comics in stack order
- show neutral unavailable messaging for filtered items
- keep edit/reorder/delete controls hidden from non-owners

Admin surface:

- simple shared-stack table with owner, title, shared date, status, and unpublish action
- avoid building a full moderation queue unless `needs_review` becomes real

## Implementation Sequence

### 1. Public Username

- add nullable `users.public_username`
- add nullable `users.public_username_created_at`
- add validation schema/service
- add first-set API
- add tests for uniqueness, reserved words, email-like values, and blocked terms

### 2. Share Metadata

- add share fields to `pull_lists`
- generate non-enumerable share tokens
- add share/unshare owner endpoints
- keep existing private stack APIs unchanged
- add tests proving another user still cannot access owner-only routes

### 3. Shared Stack Viewer

- add authenticated shared-stack route/API
- apply viewer library and age filters to visible items
- preserve sort order
- return neutral unavailable state when zero items are visible
- add API tests with owner, allowed viewer, restricted viewer, and unrelated user

### 4. Moderation Guard

- add public-text validation helper
- run it when setting public username
- run it when sharing a stack
- run it when editing an already-shared stack
- add admin unpublish behavior
- add tests for blocked usernames, blocked shared titles, and blocked shared descriptions

### 5. UI

- add username setup modal
- add share/copy/unshare controls to stack detail
- add shared stack page
- add optional shared marker to stack cards
- add focused Playwright coverage for the owner and viewer flows

## Future Options

Useful next layers if MVP sharing proves worthwhile:

- editable public usernames with cooldowns and audit history
- public handles in URLs, such as `/u/{public_username}/stacks/{slug}`
- public profile page listing a user's shared stacks
- public/discoverable stack directory
- stack search by title, creator, publisher, character, or visible items
- copy/fork a shared stack into the viewer's private stacks
- report shared stack
- admin moderation queue
- server-level setting to disable sharing entirely
- anonymous public links with an explicit default age policy
- per-library or per-user permission to publish stacks
- share expiration dates
- share analytics visible only to the owner

## Non-Goals For MVP

- anonymous web sharing
- public discovery/search
- comments, likes, follows, ratings, or trending shared stacks
- changing how private stacks work
- changing existing reader context behavior
- exposing owner email, login username, real name, or internal user ID
- exact hidden-content counts for viewers
- external moderation providers
- a public API for scraping all shared stacks

## Validation Targets

Unit/API coverage:

- public username can be set once
- public username is unique and moderated
- public username cannot default from private login fields
- private stack routes remain owner-only
- sharing requires a public username
- sharing creates a non-enumerable token
- unsharing makes old links stop working
- shared stack title/description moderation runs on publish
- editing an already-shared stack re-runs public moderation
- shared viewer sees only comics allowed by library access and age restrictions
- restricted viewer does not receive exact hidden-item counts
- zero-visible shared stack returns neutral unavailable state
- admin can unpublish a shared stack

Browser coverage:

- owner sets public username and shares a stack
- owner copies a shared link
- another signed-in user opens the shared link
- restricted viewer sees only allowed items
- owner unshares and the link stops working
- blocked title/description prevents sharing with a useful error

Manual validation:

- mixed-age stack
- stack where every item is hidden from a restricted viewer
- stack with comics from a library not assigned to the viewer
- username collision
- username containing blocked content
- shared stack rename after link creation
- deleted owner account or deleted stack cleanup

## Open Questions

- Should shared stack descriptions be hidden when a viewer has zero visible items?
- Should unsharing preserve the old token for future resharing or always generate a new token?
- Should admins be able to reserve or block public usernames?
- Should server admins be able to disable sharing globally?
- Should superusers bypass age restrictions on shared stack viewer pages as they do elsewhere?
- Should shared stacks appear in global search later, or only in a dedicated discovery page?
- Should public username changes ever be allowed, and if so should old public URLs redirect?
- Should private stack names that fail public moderation remain editable privately but blocked from sharing?

## Recommendation

Build the first pass as authenticated, unlisted, instance-local sharing.

Require a one-time public username before sharing, keep it uneditable in the MVP, and use non-enumerable share links for stacks. Filter shared stack contents per viewer, avoid exact hidden-item counts, and keep moderation focused on the username plus stack name/description at the moment they become public.
