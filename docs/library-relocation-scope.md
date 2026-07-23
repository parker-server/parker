# Library Relocation Scope

Status: Phase 1 implemented (schema + guardrail); relocation flow not yet built

This note captures a future enhancement for safely changing the filesystem path of an existing Parker library without losing comic identity.

## Implementation Status

Phase 1 (schema + guardrail) is done:

- `library_roots` table added; `Comic.library_root_id` and `Comic.relative_path` added (migration `d4a1f6e8b3c2`).
- Migration backfills one root per existing library and each comic's relative path (see `app/core/path_utils.py:compute_relative_path`).
- `create_library` now creates a matching `LibraryRoot`, and the metadata writer populates `library_root_id`/`relative_path` for every newly scanned or updated comic going forward — not just the historical backfill.
- Editing a library's path is hard-blocked in the API and admin UI (400 on change) until the relocation flow below exists.
- `library_root_id`/`relative_path` are written but not yet read by anything — `LibraryScanner` still matches on `Comic.file_path`, unchanged. See Compatibility Field Removal Plan for what's still ahead.

The immediate user problem is simple: admins can edit a library path today, either by typing a new path or by using the admin folder browser. Parker accepts the change, but the next scan can treat all old files as deleted and all files under the new path as new imports.

That behavior is understandable from the current data model, but it is risky because user state hangs from existing `Comic.id` rows.

## Why This Exists

Parker currently stores physical file identity primarily as `Comic.file_path`.

If a library moves from one server-visible path to another, the same archive can look like a completely different comic:

- old path: `/comics/DC/Batman/Batman 001.cbz`
- new path: `/mnt/comics/DC/Batman/Batman 001.cbz`

The comic content and logical position are the same, but the stored absolute path changed.

Today, changing `Library.path` does not rewrite existing comic paths. During the next scan, `LibraryScanner` scans the new root, builds a set of discovered paths, and removes existing comics whose old paths were not seen. The scanner then imports matching files at their new paths as new rows.

That can break continuity for:

- reading progress
- bookmarks
- Parker ratings
- reading list, stack, and collection membership
- other future user-attached comic state

## Product Framing

Relocation should be an explicit admin operation, not just a silent side effect of editing a text field.

Recommended UI framing:

- `Edit Library` for name and scanning/settings changes
- `Relocate Library Path` or `Relocate Root` for path moves

The relocation flow should preview impact before writing changes.

## Recommended Direction

Treat this as a library root identity problem.

Introduce a durable root record and store comic paths relative to that root:

- `Library` remains the logical library
- `LibraryRoot` represents a physical filesystem root
- `Comic.library_root_id` points to the physical root that contains the file
- `Comic.relative_path` stores the path under that root
- `Comic.file_path` may initially remain as a cached absolute path for compatibility

This allows Parker to preserve `Comic.id` when only the root path changes.

## Suggested Data Model

Add `library_roots`:

- `id`
- `library_id`
- `path`
- `is_active`
- `created_at`
- `updated_at`
- optional: `last_scanned_at`
- optional: `last_scan_error`

Add to `comics`:

- `library_root_id`
- `relative_path`

Keep initially:

- `libraries.path`
- `comics.file_path`

The compatibility period should be as short as practical, but keeping these fields during the transition will reduce blast radius.

## Migration Strategy

The first migration should preserve current behavior.

1. Create `library_roots`.
2. Backfill one root for each existing library from `libraries.path`.
3. Add nullable `Comic.library_root_id` and `Comic.relative_path`.
4. Backfill each comic by comparing `Comic.file_path` to its library root path.
5. Keep unmatched comics visible and report them as migration warnings instead of deleting them.
6. Update scanner and readers to tolerate both old and new fields during transition.
7. Once stable, make `library_root_id` and `relative_path` required for newly scanned comics.

## Relocation Flow

A safe relocation should look like this:

1. Admin selects an existing library root.
2. Admin chooses a new root path.
3. Parker computes each existing comic's expected new path from `new_root / relative_path`.
4. Parker previews:
   - matched files at the new root
   - missing files at the new root
   - new files found under the new root
   - conflicts or duplicate candidates
5. Admin confirms.
6. Parker updates the root path and cached `Comic.file_path` values for matched comics.
7. Parker queues or recommends a scan.

Important rule:

- relocation should preserve existing `Comic.id` for matched files

That is the main reason to build a relocation flow instead of relying on delete/re-import scanning.

## Matching Rules

Initial matching should be conservative:

1. match by `library_root_id + relative_path`
2. optionally verify file size and modified time when available
3. do not use content hashes in the first implementation unless there is already a hashing system

If a file is missing from the new root, the preview should not delete it automatically.

If a file appears at the new root but has no existing relative-path match, it should be treated as a new import candidate.

## Scanner Impact

The scanner should stop using absolute path as the only durable identity.

During scanning:

- resolve the active root
- compute relative path for each archive
- find existing comic by `(library_root_id, relative_path)`
- update cached absolute `file_path` if needed
- preserve `Comic.id` when the relative identity matches

Cleanup should operate within the relevant root identity, not by comparing old absolute paths against new absolute paths.

## Compatibility Field Removal Plan

`Library.path` and `Comic.file_path` are not meant to be permanent. The end state is both columns removed once nothing depends on them. Getting there is three stages, not one:

1. **Identity cutover.** `LibraryScanner`, the metadata writer's existing-comic lookup, and the maintenance janitor's missing-file cleanup switch from keying on `Comic.file_path` to `(library_root_id, relative_path)`. This is the stage that actually fixes the data-loss bug described at the top of this doc — everything after it is cleanup, not correctness.
2. **Read-path cutover.** Every remaining reader of `file_path`/`Library.path` — the comic reader/page-count lookup, thumbnail generation, comic detail/report API responses, OPDS feed entries, and their templates — switches to reconstructing the absolute path on demand from `library_root.path + relative_path` instead of reading the cached column. Needs a small companion helper to `compute_relative_path` for the reverse operation.
3. **Column removal.** Once nothing reads either field, a final migration drops `Comic.file_path` and `Library.path` for good.

Stage 1 is required before relocation can be considered safe. Stages 2 and 3 should be scheduled explicitly rather than left open-ended — a "temporary" compatibility field that nothing forces out tends to become permanent.

## Admin UI Guardrails

Short term, Parker should warn when an admin edits the path field.

Long term, the path field should move behind explicit actions:

- `Relocate Root`
- `Disable Root`
- `Remove Root`

The relocation action should have an impact preview and confirmation step.

## Relationship To Multi-Root Libraries

Library relocation should come before multi-root library support.

It introduces the root table, relative path identity, scanner lookup rules, and safe admin preview patterns that multi-root support will need anyway.

Once relocation exists, multi-root support becomes an extension from one root per library to many roots per library.

Relocation does not become unnecessary once multi-root ships — it stays a required primitive, not a stepping-stone that gets superseded. Multi-root introduces "remove a root" and "add a root" as admin actions, and if removing a root deletes the comics under it, then "remove root, move files, add a new root, rescan" reintroduces the exact same data-loss hazard this doc exists to prevent, just triggered by a different button. Multi-root arguably makes this *more* likely to come up, not less — swapping or reorganizing one root among several (splitting across drives, consolidating storage) is a more routine operation than editing a single-root library's only path ever was. `Relocate Root` and `Remove Root` need to stay distinct, explicit admin actions: the system can't safely infer "this new root is the old one, just moved" on its own — matching by `relative_path` without that explicit signal risks silently merging coincidentally-identical paths from genuinely unrelated content. So relocation should be built as a general root-lifecycle primitive from the start, not a single-root-era feature to retire once multi-root lands.

## Non-Goals

Out of scope for an initial relocation feature:

- content-hash deduplication
- merging two separate Parker libraries into one
- root-level permissions
- automatic filesystem discovery outside configured roots
- rewriting user state by fuzzy metadata matches

## Testing Notes

Minimum coverage should include:

- migration backfills one root per existing library
- migration backfills comic relative paths under the root
- relocation preview counts matched, missing, and new files
- confirmed relocation preserves `Comic.id`
- reading progress/bookmarks/ratings remain attached after relocation
- scanner does not delete matched comics after root relocation
- path overlap validation still prevents ambiguous ownership

## Open Questions

- Should relocation be allowed while a library scan is running?
- Should Parker allow relocation when some files are missing from the new root?
- Should relocation update `last_scanned` or require a follow-up scan?
- Should unmatched files remain in the database as disabled/missing records, or continue using current cleanup behavior after confirmation?

## Effort Estimate

This is smaller than full multi-root support but still a meaningful schema and scanner project.

It is best treated as a deliberate feature, not a small admin-form enhancement.
