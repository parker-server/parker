# Multi-Root Library Scope

Status: Foundation in place, product surface pending

This note tracks Parker's path from one physical folder per library to one logical library with multiple configured filesystem roots.

Related design note: `docs/library-relocation-scope.md`.

## Why This Exists

Some users may want one logical library to span more than one storage location:

- comics split across multiple drives
- NAS and local-storage hybrids
- active and archive folders that should still appear as one library
- staged imports that should belong to an existing library instead of a second library tile

The useful framing is:

- one logical library
- many explicit storage roots

Parker should not auto-discover arbitrary storage locations. Admins should intentionally attach each root to a library.

## Current System Shape

The relocation groundwork has already moved Parker away from `Library.path` as the physical identity:

- `Library` is the logical library.
- `LibraryRoot` represents a physical filesystem root.
- `Library.roots` stores all configured roots.
- `Library.active_root` remains a compatibility helper for single-root surfaces.
- `Library.active_roots` returns every active root for services that can process multiple roots.
- `Series.library_id` remains attached to the logical library.
- `Comic.library_root_id` identifies the root containing the archive.
- `Comic.relative_path` identifies the archive under that root.

Physical file identity is:

- `(library_root_id, relative_path)`

That identity must remain the backbone for scanner cleanup, metadata writes, and future root lifecycle operations.

## Foundation Behavior

The foundation layer should behave as if multiple active roots can already exist, even before the admin UI exposes full root management.

Scanner behavior:

- load all active roots for the library
- verify every active root path exists before discovery or cleanup
- walk each active root independently
- compute `relative_path` under the root being scanned
- match existing comics by `(library_root_id, relative_path)`
- pass root context into metadata worker jobs
- cleanup only after all active roots were reachable
- mark each active root with the completed scan timestamp

Metadata behavior:

- preserve scanner-provided root context through worker results
- write comics using the item root identity
- use the matching physical root as the sidecar boundary
- allow the same `relative_path` to exist under different roots

Watcher behavior:

- schedule one watchdog subscription per active root
- key watch bookkeeping by `(library_id, root_id)`
- keep queueing scan jobs by `library_id`
- let existing scan coalescing handle events from multiple roots

Diagnostics behavior:

- report root counts
- include per-root path, active state, and existence checks
- retain the legacy primary `path`/`path_exists` fields while single-root admin surfaces still depend on them

Janitor cleanup behavior:

- resolve physical comic paths from `library_root_id` plus `relative_path`
- verify active roots for the cleanup scope before deleting missing-file records
- abort missing-file cleanup if any active root in that scope is unreachable

## Safety Rules

An active root that is offline is different from a deleted comic.

Scanner and janitor missing-file cleanup should fail safely if any active root in scope is unreachable. They should not prune comics from reachable roots or unreachable roots during that run, because the missing root may be an offline drive, unmounted share, or temporary permissions issue.

Recommended rule:

- all active roots reachable: scan and cleanup normally
- any active root in scope unreachable: fail before cleanup and leave database rows untouched

This is intentionally conservative. It protects users from accidental mass deletes caused by partial storage availability.

## Sidecar Boundary Rule

Sidecars apply within the physical root containing the file being processed.

For a comic in root A, parent walking stops at root A. For a comic in root B, parent walking stops at root B. There is no virtual library-wide filesystem boundary across every root.

This keeps multi-root behavior close to existing single-root behavior.

## Permissions

Parker's access model remains library-based.

Recommended MVP rule:

- if a user can access the library, they can access content imported from any active root attached to that library

Root-level permissions are out of scope for the initial multi-root feature.

## Non-Goals

Out of scope for initial multi-root support:

- loose image reading outside Parker's existing archive model
- metadata/entity-level merging of separate libraries
- root-level user permissions
- automatic discovery of arbitrary folders
- content-hash deduplication
- automatic file moves between roots

## Remaining Product Work

The foundation does not complete multi-root as an admin-facing feature.

Remaining work:

- add root management API actions
- add admin UI for listing roots
- add explicit add, disable, relocate, and remove flows
- validate new roots against every configured root in the system
- reject overlapping roots within the same library
- reject overlapping roots across libraries
- decide whether zero-root libraries are allowed during editing
- define what happens to comics when a root is disabled or removed
- update support/admin views so partial root failures are understandable

Root lifecycle actions should be explicit. A generic library edit form should not silently change or remove storage roots.

## Duplicate Policy

Duplicate-looking comics can already exist inside one root. Multi-root support makes the policy more visible but does not require content-level deduplication.

Recommended MVP stance:

- prevent root overlap up front
- treat `(library_root_id, relative_path)` as the physical file identity
- allow the same relative path under different non-overlapping roots
- do not attempt archive hash or page hash deduplication in the first release
- consider duplicate reporting later if users need it

## Background Jobs

Multi-root libraries should still behave like one library to the scheduler:

- scan jobs are queued per library
- thumbnail jobs remain library-scoped
- metadata rehydrate remains library-scoped

A library with several large roots may be operationally heavier than a small single-root library. That may eventually justify better scan progress reporting, but it should not block the MVP.

## Recommended Implementation Order

1. Keep the relocation/root-identity foundation in place.
2. Make scanner, metadata writer, watcher, diagnostics, and janitor cleanup root-list aware.
3. Add root management API actions.
4. Add admin UI for root list and root lifecycle operations.
5. Add overlap validation across all roots.
6. Decide disable/remove/offline root policy.
7. Improve diagnostics and support messaging for partial root failures.
8. Add broader browser coverage once the UI exists.

Items 1 and 2 are the service foundation. Items 3 through 8 are the remaining product feature.

## Testing Notes

Foundation coverage should include:

- scanner imports and cleanup across multiple active roots
- scanner failing before cleanup when any active root is unreachable
- janitor missing-file cleanup failing before deletion when any active root in scope is unreachable
- metadata worker preserving root context
- metadata writer importing the same relative path under different roots
- sidecar resolution stopping at the current physical root
- watcher registering and unregistering one watch per active root
- diagnostics reporting root counts and per-root existence checks

Product/UI coverage should later include:

- root add/disable/remove APIs
- overlap validation against same-library and cross-library roots
- admin root list rendering
- browser coverage for expected root lifecycle flows
