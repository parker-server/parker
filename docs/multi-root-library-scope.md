# Multi-Root Library Scope

Status: Draft

This note captures a possible future enhancement where one Parker library can aggregate comics from multiple filesystem roots instead of a single configured folder.

Related design note: `docs/library-relocation-scope.md`.

Recommended sequencing: build graceful library-root relocation before full multi-root support. Relocation introduces the root identity and relative-path model that multi-root support should reuse.

The goal is to document the likely impact area before real user demand exists, so the work can be evaluated intentionally later instead of being estimated from memory.

## Why This Exists

Some users may eventually want one logical library to span more than one folder location.

Examples:

- comics split across multiple drives
- NAS and local-storage hybrids
- "active" and "archive" roots that should still appear as one library
- staged imports that should belong to an existing library instead of a second library tile

This is not currently a known blocker for Parker adoption.
It is mainly a parity observation against tools like Kavita and a reminder that Parker was originally designed around one library path per library.

## Current System Shape

Today Parker models one library as one root path.

That assumption appears directly in several places:

- `Library.path` is a single string field in `app/models/library.py`
- library create/update validation checks one path and rejects overlapping library paths in `app/api/libraries.py`
- library scans walk exactly one root with `Path(self.library.path).rglob("*")` in `app/services/scanner.py`
- watch mode schedules one watchdog subscription per library in `app/services/watcher.py`
- metadata-sidecar reconciliation uses the library path as a boundary in `app/services/scanner.py` and `app/services/workers/metadata_writer.py`
- startup diagnostics sample one path per library in `app/services/startup_diagnostics.py`

This means multi-root support is not just an admin-form change.
It would change one of Parker's basic storage assumptions.

## Non-Goals

Out of scope for an initial multi-root implementation:

- support for loose image reading outside Parker's existing archive model
- merging separate libraries at the metadata/entity level
- deduplicating identical comics by page/hash/content fingerprint
- automatic unioning of arbitrary folders without admin configuration
- changing user permissions from library-based access to root-based access

## Product Framing

If Parker adds this feature, the better framing is likely:

- one logical library
- many storage roots

Not:

- one library automatically discovers storage everywhere

The admin should still explicitly decide which roots belong to a library.

## Suggested Data Model

Recommended direction: add a dedicated child table rather than overloading `Library.path`.

This should align with the relocation design:

- `Library` stays the logical library
- `LibraryRoot` represents a physical filesystem root
- `Series.library_id` remains attached to the logical library
- `Comic.library_root_id` identifies the root containing the physical archive
- `Comic.relative_path` identifies the archive under that root

In other words, the hierarchy remains:

- `Library -> Series -> Volume -> Comic`

Storage roots sit beside that hierarchy:

- `Library -> LibraryRoot`
- `Comic -> LibraryRoot`

Example shape:

- keep `libraries` as the logical library record
- add `library_roots`
- each root belongs to one library
- each root stores a normalized path string plus any root-specific metadata we eventually need

Possible columns:

- `id`
- `library_id`
- `path`
- `created_at`
- `updated_at`

Optional future columns if needed:

- `enabled`
- `last_scan_error`
- `last_seen_at`

Why this is preferable:

- preserves the existing meaning of `Library`
- allows one-to-many roots cleanly
- gives watcher/scanner code a first-class structure to target
- avoids stuffing serialized path arrays into one column

## Migration Strategy

The least risky migration would be shared with the library relocation work:

1. create `library_roots`
2. backfill one root row from each existing `libraries.path`
3. add `Comic.library_root_id` and `Comic.relative_path`
4. backfill each comic's root and relative path
5. keep `libraries.path` and `comics.file_path` temporarily for compatibility during the transition
6. update application code to read roots from the new table
7. remove or deprecate direct `Library.path` usage after the codebase is fully migrated

The exact length of the compatibility period is a product decision.
If the code churn is manageable, it may be cleaner to migrate quickly rather than support dual behavior for too long.

## Scanner Impact

The scanner is one of the main change points.

Today `LibraryScanner.scan_parallel()` in `app/services/scanner.py`:

- resolves one `library_path`
- walks one root recursively
- builds one set of `scanned_paths_on_disk`
- treats any missing previously known file as deleted

With multi-root support, Parker would likely need to:

- resolve all roots for the library
- scan each root recursively
- compute `relative_path` under the current root for each archive
- match existing comics by `(library_root_id, relative_path)`
- preserve one cleanup pass that understands root identity

Important nuance:

- cleanup should treat a file as missing only within the root that owns that comic
- moving a file between roots is a different operation from a normal scan and should require a deliberate relocation or reconciliation flow

## Watcher Impact

Watch mode would also need a structural change.

Today `LibraryWatcher` in `app/services/watcher.py` effectively keeps:

- one library id
- one scheduled watch
- one event handler

Multi-root support would likely require:

- one library id
- many scheduled watch objects
- possibly one handler per root, all queueing the same library scan

Recommended behavior:

- keep queueing scans by `library_id`, not by root
- let multiple root events coalesce into the same existing batch-window behavior

The bookkeeping changes are not conceptually hard, but they are easy to get subtly wrong.

## Sidecar Boundary Impact

Parker currently uses the library root as a boundary when reconciling series and volume sidecars.

Relevant code lives in:

- `app/services/scanner.py`
- `app/services/workers/metadata_writer.py`

This means multi-root support should define:

- sidecars only apply within the root that contains the file being processed
- parent walking stops at that specific root boundary, not a generic library-wide virtual boundary

That is probably the cleanest rule and stays close to current behavior.

## Admin API And UI Impact

The admin library flows in `app/api/libraries.py` currently assume:

- one path on create
- one path on edit
- overlap validation against other libraries using one candidate path

Multi-root support would need:

- create/edit APIs that expose a collection of roots
- explicit root actions such as add, relocate, disable, and remove
- root add/remove/update UI
- validation that checks every candidate root against every existing root in the system

Recommended guardrails:

- reject roots that overlap one another, even inside the same library
- reject roots that overlap another library's roots
- normalize path comparisons before validating
- avoid silently changing root paths through a generic library edit form

This keeps the mental model simple and avoids ambiguous ownership of the same subtree.

## Duplicate Policy

Duplicate imports are not unique to multi-root libraries.
They can already happen today when the same archive exists in different subfolders under one root.

What multi-root changes is the importance of defining the policy clearly across all scanned paths.

Questions Parker should answer before implementation:

- if the same file appears in two configured roots, should both imports be allowed?
- should Parker warn only, or block overlapping/duplicate-looking roots up front?
- should Parker eventually detect likely duplicates by normalized file path, filename, size, archive hash, or not at all?

Recommended initial stance:

- do not attempt content-level deduplication as part of the first multi-root release
- keep prevention focused on root-overlap validation
- treat broader duplicate detection as a separate problem
- use `(library_root_id, relative_path)` as the physical file identity, not absolute path alone

## Diagnostics And Support Impact

Startup and support diagnostics currently report one path per library in `app/services/startup_diagnostics.py`.

Multi-root support would likely need:

- library root counts
- a per-root sample instead of one path string
- per-root existence checks
- clearer support messaging when only some roots are reachable

This matters because "library exists but one root is offline" becomes a valid operational state.

## Permissions And User Access

The current permission model is library-based, not path-based.

That is a strength here.
Parker likely should not introduce root-level permissions in an MVP.

Recommended rule:

- if a user can access the library, they can access content imported from any of that library's configured roots

This keeps the feature focused on storage aggregation rather than access-control redesign.

## Background Jobs And Fairness

Multi-root support should continue to behave like one library from the job scheduler's perspective.

That means:

- scan jobs are still queued per library
- thumbnail jobs remain library-scoped
- metadata rehydrate remains library-scoped

The nuance is performance fairness:

- a library with many large roots may become heavier than a single-root library
- this is mostly an operational concern, not a reason to block the feature

It may eventually justify better scan progress reporting or per-root progress visibility, but that should not be required for an MVP.

## Suggested MVP

The smallest useful implementation would likely be:

- support multiple configured roots per library
- update scanner to walk all roots and union results
- update watcher to monitor all roots and still queue by library
- update admin create/edit flows for multiple roots
- update diagnostics to surface roots clearly
- keep permissions library-based
- keep duplicate handling conservative and simple

This MVP assumes the relocation groundwork exists first. If it does not, the MVP should begin by introducing `library_roots`, `Comic.library_root_id`, and `Comic.relative_path` before adding multiple roots to the UI.

## Open Questions

- Should an empty library be allowed to exist temporarily with zero roots during editing?
- Should root ordering matter in the UI or for future diagnostics?
- Should Parker allow disabling one root without removing it?
- Should the library detail API continue to expose one legacy `path` field during transition, or move directly to `roots`?
- How much backward compatibility is worth carrying for older clients or templates that expect `library.path`?

## Recommended Implementation Order

If Parker ever decides to build this, a safe order would likely be:

1. implement safe single-root relocation as described in `docs/library-relocation-scope.md`
2. keep one root per library until scanner, reader, watcher, and diagnostics understand root identity
3. add admin UI/API support for adding more roots to an existing library
4. update scanner cleanup behavior across root identities
5. update watcher scheduling/bookkeeping
6. update diagnostics and support surfaces
7. add regression coverage

## Testing Notes

Minimum coverage should include:

- migration/backfill behavior from one path to one root row
- scanner imports across multiple roots in one library
- cleanup only deleting comics absent from all roots
- watcher refresh registering and unregistering multiple watches for one library
- sidecar reconciliation stopping at the correct root boundary
- admin validation rejecting overlapping roots

## Effort Estimate

This looks like a medium-to-large feature rather than a quick enhancement.

Rough estimate:

- MVP: moderate project with several touching systems
- polished release: larger due to migration, watcher edge cases, admin UX, diagnostics, and regression coverage

In practical terms, this is probably best treated as a deliberate feature project with a design pass first, not something to casually slip into a small release.
