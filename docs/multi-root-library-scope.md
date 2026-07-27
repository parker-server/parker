# Multi-Root Library Scope

Status: Root lifecycle product surface in place

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

That identity must remain the backbone for scanner cleanup, metadata writes, and root lifecycle operations.

## Foundation Behavior

The foundation layer behaves as if multiple active roots can exist.

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
- skip comics tied to inactive roots during missing-file cleanup

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

MVP rule:

- if a user can access the library, they can access content imported from any root attached to that library

Root-level permissions are out of scope for the initial multi-root feature.

## Non-Goals

Out of scope for initial multi-root support:

- loose image reading outside Parker's existing archive model
- metadata/entity-level merging of separate libraries
- root-level user permissions
- automatic discovery of arbitrary folders
- content-hash deduplication
- automatic file moves between roots

## Root Lifecycle Policy

Root lifecycle actions are explicit. A generic library edit form does not silently change or remove storage roots.

Implemented admin/API behavior:

- Add root: creates a new active `LibraryRoot`, rejects overlap with any configured root in any library, preserves existing comics, and does not start a scan automatically.
- Disable root: marks the root inactive for scanner and watcher discovery. Existing comics tied to that root remain in the database and remain visible through normal library access.
- Enable root: marks a disabled root active again after validating that its stored path does not overlap another configured root.
- Relocate root: updates one selected root path through the relocation preview/confirm flow. Existing comic records are preserved and no scan is started automatically.
- Remove root: empty roots can be removed directly. Roots with comics are rejected unless `delete_comics=true`, which deletes those comic records and prunes now-empty volumes and series. Files on disk are never deleted.
- Zero active roots: allowed. This lets an admin temporarily pause an offline or intentionally detached library without inventing a fake path.

Overlap validation applies to active and inactive roots so a disabled historical root cannot silently conflict with a future attachment.

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

## Implementation Status

Completed:

- relocation/root-identity foundation
- scanner, metadata writer, watcher, diagnostics, and janitor cleanup root-list awareness
- root management API actions
- admin UI for root listing and lifecycle operations
- overlap validation across active and inactive roots
- disable/remove/offline root policy
- browser coverage for relocation and root lifecycle flows

Future refinements should be driven by real use, especially around duplicate reporting, scan progress for very large multi-root libraries, and support messaging for unusual partial-storage failures.

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

Product/UI coverage includes:

- root add/disable/remove APIs
- overlap validation against same-library and cross-library roots
- admin root list rendering
- browser coverage for expected root lifecycle flows
