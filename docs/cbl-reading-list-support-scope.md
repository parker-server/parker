# CBL Reading List Support Scope

Status: Draft

This note captures a possible path for adding CBL reading list support to Parker without weakening the "Filesystem is Truth" model.

## Why This Exists

Parker can already generate reading lists from embedded ComicInfo metadata using `AlternateSeries` and `AlternateNumber`. That works well for carefully tagged libraries, but many users do not receive or maintain comic archives with complete event chronology metadata.

CBL files give those users a practical bridge:

- community or curator-provided reading orders can be stored in Parker
- Docker users can keep comic volumes mounted read-only
- Parker can offer useful event reading without editing archives
- meticulous users can keep using embedded ComicInfo metadata as their preferred source

The intended product framing is not "CBL repairs metadata." It is "CBL files are reading-list source files that Parker stores and indexes."

## Product Principle

CBL support should preserve this source-of-truth hierarchy:

- comic archives and embedded `ComicInfo.xml` remain the source for per-comic metadata
- `.cbl` files in Parker-managed CBL storage are the source for CBL-derived reading-list order
- optional `.cbl` files discovered under comic library roots may be imported/copied into that managed storage
- Parker's database stores derived index/runtime state

Parker should not mutate comic archives as part of CBL support. This is especially important for Docker deployments where library mounts are commonly read-only.

CBL storage should be separate from comic series folders. Comic events often span many titles, publishers, imprints, and volumes, so a series folder is the wrong ownership boundary. Parker should keep CBL files in a dedicated writable area such as `storage/cbl` or a configurable `settings.cbl_dir`.

## Current System Shape

Important existing touch points:

- `app/models/reading_list.py`
- `app/models/comic.py`
- `app/services/reading_list.py`
- `app/services/workers/metadata_writer.py`
- `app/api/reading_lists.py`
- `app/templates/reading_lists/reading_list_detail.html`
- `app/templates/partials/reading_list_card.html`
- `app/services/maintenance.py`

Current behavior:

- `ReadingList.auto_generated` is an integer boolean.
- `auto_generated = 1` effectively means generated from ComicInfo `AlternateSeries`/`AlternateNumber`.
- `auto_generated = 0` means manually created or user-owned.
- `ReadingListService.update_comic_reading_lists()` treats a comic's reading-list memberships as owned by the ComicInfo-derived metadata path and replaces existing list items when the target metadata changes.
- `Library.parse_reading_lists` controls whether metadata-derived reading list membership is visible/maintained for that library.

That boolean is now too small for the product concept. Reading lists need provenance.

## Data Model Direction

Replace or extend `ReadingList.auto_generated` with explicit source metadata.

Recommended fields:

- `source`: string enum-like value, non-null, indexed
- `source_cbl_id`: nullable foreign key to a managed CBL source record
- `source_origin`: nullable string for CBL files, for example `upload`, `url`, `catalog`, or `library_import`
- `source_url`: nullable string for URL/catalog-acquired CBL files
- `source_modified_at`: nullable float/datetime for scan change detection
- `source_fingerprint`: nullable string for content hash or cheap checksum

Recommended source values:

- `manual`: user-created list, Parker must not overwrite it during scans
- `comicinfo`: derived from archive metadata fields such as `AlternateSeries` and `AlternateNumber`
- `cbl`: derived from a managed `.cbl` source file

Consider a separate `cbl_sources` table instead of putting every source field directly on `reading_lists`.

Useful `cbl_sources` fields:

- `id`
- `display_name`
- `stored_path`: path under Parker-managed CBL storage
- `original_filename`
- `origin`: `upload`, `url`, `catalog`, or `library_import`
- `source_url`: original URL for URL/catalog imports
- `catalog_provider`: for example `dieseltech`
- `catalog_path`: repository path for catalog imports
- `imported_at`
- `last_refreshed_at`
- `last_refresh_status`
- `fingerprint`
- `entry_count`
- `last_match_summary`

`ReadingList.source_cbl_id` can then point at the CBL source that owns the derived list. This avoids stretching `reading_lists` into a file-management table.

Migration rule:

- existing `auto_generated = 1` rows become `source = "comicinfo"`
- existing `auto_generated = 0` rows become `source = "manual"`
- rows with null `auto_generated` should be treated as `comicinfo` only if existing behavior did so; otherwise default to `manual` conservatively

Compatibility option:

- Keep `auto_generated` for one release as a compatibility field while APIs begin returning `source` and `source_label`.
- Alternatively, remove `auto_generated` immediately if internal and external callers are updated in the same release.

API/UI behavior:

- expose `source`
- expose `source_label`, for example `Manual`, `Auto-Generated`, and `CBL Derived`
- keep `auto_generated` only as a transitional response field if needed

## Ownership Rules

Each derived path should only modify rows that it owns.

ComicInfo scanner:

- creates and updates `source = "comicinfo"` reading lists
- updates items derived from `AlternateSeries`/`AlternateNumber`
- must not delete or alter `manual` or `cbl` memberships

CBL scanner:

- creates and updates `source = "cbl"` reading lists
- updates items for the matching `source_cbl_id`
- must not delete or alter `manual` or `comicinfo` memberships

Manual UI/API actions:

- create `source = "manual"` lists
- should either reject editing derived lists by default or require an explicit "detach from source" action

This is the key behavior change. The current per-comic replacement logic is safe for one generated source, but it will need source-aware membership updates before CBL support lands.

## CBL Acquisition And Discovery

MVP acquisition should start with Parker-managed CBL storage:

- add a dedicated writable CBL storage directory, probably `storage/cbl`
- add a config field such as `cbl_dir: Path = Path("storage/cbl")` if the path needs to be configurable
- store every uploaded, URL-downloaded, catalog-imported, or library-imported CBL file there
- parse and match from the managed stored copy
- never require writing a CBL file into a comic library root

Recommended acquisition paths:

- upload a `.cbl` file from the admin UI
- import a direct `.cbl` URL
- browse a curated remote catalog such as `DieselTech/CBL-ReadingLists` and import selected files
- optionally import/copy existing `.cbl` files found under comic library roots

Downloaded or discovered CBL files should be copied into Parker-managed CBL storage rather than written into comic library roots. This keeps read-only Docker comic mounts viable while still making the persisted CBL file, not a one-time DB import, the durable source Parker re-indexes.

Recommended managed storage behavior:

- store imported CBL files under Parker's normal writable storage path
- keep original filename, source URL, source repository path, fingerprint, and import timestamp
- let admins refresh, replace, or delete managed CBL files from Parker
- make managed CBL files the canonical parse/match source

Use `Library.parse_reading_lists` as the initial master switch for both ComicInfo-derived and CBL-derived reading lists. If users later need separate controls, add a dedicated `parse_cbl_reading_lists` library flag in a focused follow-up.

Library discovery behavior:

- scanning `.cbl` files under active library roots should be optional and treated as an import source, not canonical storage
- if enabled, discovery should copy new/changed `.cbl` files into managed storage and then parse from that copy
- deleting a library-side `.cbl` after import should not delete the managed copy unless Parker explicitly tracks and enables that synchronization policy
- a missing active root should not prune managed CBL sources

Managed CBL behavior:

- changes made through Parker can queue a CBL refresh directly
- remote refresh should be explicit or scheduled conservatively, not performed during every normal comic scan
- remote unavailability should mark refresh as failed without deleting the last successfully imported CBL-derived list

## Remote Catalog Browser

Browsing `DieselTech/CBL-ReadingLists` from the Parker UI would be a useful later layer on top of direct CBL import.

The repository is organized by publisher/imprint and category folders such as Marvel, DC, Image, Dark Horse, IDW, Valiant, Vertigo, and others. Its README describes the project as a curated source of reading lists for comic readers and management software, with lists provided in CBL format and book data verified on ComicVine.

Recommended MVP behavior:

- add an admin-only "Browse CBL Catalog" surface
- start with a single built-in catalog provider for `DieselTech/CBL-ReadingLists`
- browse folders and `.cbl` files via GitHub's repository contents API or raw file URLs
- support search/filter by filename and path
- preview list name and entry count before importing
- import selected files into Parker-managed CBL storage
- show the source repository path on managed CBL records

Recommended caching:

- cache catalog directory listings with ETag/Last-Modified metadata where available
- cache conservatively to avoid GitHub unauthenticated rate-limit pain
- provide a manual refresh action
- do not require a GitHub token for the MVP

Catalog scope should stay intentionally narrow at first. A generic GitHub browser is not necessary for CBL support and would add avoidable security and product complexity.

Direct URL import can share most of the same implementation, but with stricter validation because the URL is user supplied.

Direct URL safeguards:

- allow `https` only
- reject private/local/link-local hosts and IP literals that resolve to private ranges
- enforce a small max download size
- enforce XML/CBL extension or content sniffing
- apply request timeouts and redirect limits
- store the downloaded content as a managed CBL file before parsing
- preserve source URL and fingerprint for future refresh/debugging

## CBL Parsing

The parser should be small and strict enough to be predictable:

- accept XML `.cbl` files
- read the list name from the file when present
- otherwise fall back to the CBL filename stem
- preserve item order from the CBL file
- ignore unsupported fields during MVP parsing
- collect parse warnings instead of failing the whole library scan when one CBL is malformed

Implementation should include real fixture files before finalizing field mapping. Common ComicRack-style CBL files describe an ordered reading list with a `Name` plus `Book` entries carrying identifying attributes such as `Series`, `Number`, `Volume`, and `Year`. Some files also include optional fields such as `Format`, `Id`, `Database`, or matchers that Parker can ignore in the MVP.

## Matching Strategy

Implemented in `app/services/cbl_matching.py`.

Real ComicRack-style CBL `<Book>` entries carry only `Series`/`Number`/`Volume`/`Year`/`Format`
attributes -- no filesystem path or filename -- so a path/filename tier (as originally
considered here) has nothing to match against for real-world CBL files and was not built.
Matching goes straight to metadata, in three tiers:

1. `series + number + volume + year`, normalized. Only works when the library's `Volume`
   tagging convention agrees with the CBL file's -- both usually meaning "the year this
   print run started."
2. `series + number + year`, dropping the volume comparison. Needed because plenty of
   libraries tag `Volume` as a plain sequential index (1, 2, 3...) instead of a start year,
   which would otherwise never agree with a CBL file's `Volume` value and silently degrade
   every match in that library to tier 3.
3. `series + number` only, as a last resort when no year is available on either side.

Each tier only ever compares values the tagger/CBL file actually asserted -- never a
derived or inferred one (e.g. approximating a volume's start year from the earliest
issue's publication year was considered and rejected: it can be confidently *wrong* for
any issue published after a volume's first year, which is worse than not matching).

Ambiguity policy:

- never guess when multiple comics match a tier's key
- an ambiguous result at any tier is terminal for that entry -- it does not fall through
  to a looser tier, since every later tier is a strict superset of the same match space
  and can only be equally or more ambiguous, never less
- skip unmatched or ambiguous entries; keep the rest of the list usable
- surface skipped entries in scan summary/admin reporting (`CBLSource.last_match_summary`)

Series-name normalization (`app/core/text_utils.py::normalize_title`) and issue-number
normalization (`app/core/comic_helpers.py::normalize_issue_number`) are shared with the
ComicInfo-derived matching path rather than duplicated.

## Generated List Naming

Preferred behavior:

- use the CBL-provided list name when present
- fall back to the filename stem
- avoid colliding with manual lists

Collision policy:

- If a matching `source = "cbl"` list exists for the same `source_cbl_id`, update it.
- If a manual list has the same name, keep the manual list untouched and create a distinguishable CBL-derived list name.
- If a ComicInfo-derived list has the same name, keep sources separate unless product testing shows merging is valuable.

Open naming question:

- Should CBL-derived and ComicInfo-derived lists with the same event name appear as two cards, or should the UI group them by display name with source tabs? MVP should prefer separate lists because it is easier to reason about and safer to delete/update.

## Delete And Stale Behavior

When a managed CBL file/source is deleted through Parker:

- delete the associated `source = "cbl"` list and its items, or
- mark it stale/disabled and hide it from normal user surfaces

Recommended MVP:

- deleting a managed CBL source deletes its derived list/items
- remote refresh failure does not delete the last successfully matched list
- library root availability should not affect managed CBL source retention

This keeps destructive behavior tied to explicit Parker-managed source deletion rather than incidental filesystem availability.

## Admin Reporting

CBL support should include a small visibility surface for imperfect imports.

Useful scan/report fields:

- CBL files discovered
- CBL files parsed
- CBL parse failures
- reading lists created
- reading lists updated
- entries matched by metadata (series + number + volume + year)
- entries matched by metadata (series + number + year)
- entries matched by relaxed metadata (series + number only)
- unmatched entries
- ambiguous entries

The user-facing result should make it clear that skipped entries are not fatal. A partial CBL list is still useful, but admins should know what Parker could not confidently resolve.

## API And UI Changes

Reading list list/detail responses should include:

- `source`
- `source_label`
- CBL source metadata for admins only, or omitted from public user responses unless useful
- transitional `auto_generated` only if kept for compatibility

UI labels:

- `manual` -> no badge, or `Manual` in admin-only contexts
- `comicinfo` -> `Auto-Generated`
- `cbl` -> `CBL Derived`

Derived list edit/delete behavior should be deliberate:

- deleting a CBL-derived list should either remove only Parker's derived DB rows until the next scan recreates it, or require deleting/disabling the source CBL file
- the UI should avoid implying that deleting the Parker list deletes the source `.cbl`
- editing a derived list should probably require detaching it to `manual`

## Implementation Sequence

### 1. Reading List Provenance

- add `ReadingList.source`
- optionally add source tracking fields for CBL files
- migrate existing rows from `auto_generated`
- update API responses and tests
- update UI badges
- make maintenance cleanup source-aware

### 2. Source-Aware ReadingListService

- update ComicInfo-derived list maintenance to operate only on `source = "comicinfo"` memberships
- prevent metadata scans from deleting manual or CBL-derived list items
- add focused unit tests around mixed-source memberships for the same comic

### 3. CBL Parser Service

- add a parser with fixture-backed tests
- normalize entries into an internal representation independent of raw XML shape
- return warnings/errors alongside parsed entries

### 4. Managed CBL Storage And Acquisition

- add Parker-managed CBL storage for uploads, URL downloads, and catalog imports
- store CBL files in a dedicated writable directory such as `storage/cbl`
- add a `cbl_sources` model/table if source management outgrows `reading_lists`
- persist source URL/repository path/fingerprint metadata
- add direct URL import with network safety checks
- add admin APIs to list, refresh, replace, and delete managed CBL files

### 5. Matching Service

- implement deterministic matching: series+number+volume+year, then series+number+year, then series+number only
- collect unmatched and ambiguous entries
- add tests for duplicate metadata, missing years, and mismatched volume-tagging conventions (sequential vs. year-based)

### 6. Scan Integration

- refresh managed CBL sources after comic metadata import so matching uses the current DB state
- optionally discover library-root `.cbl` files and copy/import them into managed storage
- update/delete only CBL-derived lists tied to the managed CBL source being refreshed
- add scan summary fields

### 7. Catalog Browser

- add an admin-only DieselTech catalog browser
- support folder navigation, search, preview, and import
- cache remote catalog listings
- handle GitHub/network failures as non-fatal admin messages

### 8. Admin/UI Polish

- show `CBL Derived` badges
- expose import warnings in jobs/admin reporting
- clarify empty-state copy so reading lists can come from metadata or CBL files

## Non-Goals For MVP

- writing `AlternateSeries` or `AlternateNumber` back into archives
- editing `.cbl` files from Parker
- merging CBL-derived and ComicInfo-derived lists automatically
- global chronology inference
- content-hash matching
- a generic unrestricted GitHub repository browser
- requiring GitHub authentication for catalog browsing
- treating comic series folders as the canonical home for event-level CBL files

## Validation Targets

Unit coverage:

- migration from `auto_generated` to `source`
- API source fields and source labels
- ComicInfo updates preserving manual and CBL-derived memberships
- CBL parser fixtures
- URL import safety checks
- managed CBL file lifecycle
- catalog listing cache behavior
- matching by metadata (strict volume+year, year-only, and relaxed series+number tiers)
- ambiguous/unmatched entry handling
- CBL delete/stale behavior for managed source deletion and failed refresh

Browser/API coverage where practical:

- reading list cards show `Auto-Generated` and `CBL Derived` badges
- CBL-derived reading list detail renders in order
- deleting or detaching a derived list follows the chosen UX policy
- admin can browse catalog folders, preview a CBL, and import it

Manual validation:

- Docker-style read-only comic mount
- managed CBL storage with events spanning multiple titles
- optional library-root CBL discovery/import, if implemented
- direct URL import from a raw `.cbl` URL
- DieselTech catalog browse/import
- malformed CBL file does not fail the whole scan
- managed CBL source deletion cleans up derived list state

## Open Questions

- Should CBL-derived lists be visible through OPDS once OPDS reading-list feeds exist?
- Should admins be able to disable CBL scanning separately from ComicInfo-derived reading lists?
- Should duplicate display names be grouped in the UI or remain separate by source?
- Should Parker offer a "detach to manual list" action for derived lists?
- What exact CBL fields should be considered authoritative after fixture validation?
- Should remote/catalog-imported CBLs refresh automatically on a schedule, or only by explicit admin action?
- Should the DieselTech catalog be built in, configurable, or represented as a first-party default plus custom catalog URLs later?

## External References

- Komga read list documentation notes that `.cbl` imports contain the read list name plus ordered books identified by series, volume, year, and number: https://komga.org/es/docs/guides/readlists/
- Kavita's CBL import guide documents a matching model based on series, number, volume, and year: https://wiki.kavitareader.com/guides/features/cbl-import/
- Mylar's ComicRack CBL import notes show the basic XML structure and confirm that item order is the reading order: https://forum.mylarcomics.com/viewtopic.php?t=27
- DieselTech's `CBL-ReadingLists` repository provides curated CBL reading lists organized by publisher/imprint and category: https://github.com/DieselTech/CBL-ReadingLists
