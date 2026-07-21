# OPDS Expansion Scope

Status: Draft

This note captures a possible expansion path for Parker's OPDS support after early Android reader testing.

The goal is to improve compatibility and usefulness for external reader apps while keeping Parker's OPDS implementation conservative. OPDS clients vary widely, so new features should be additive where possible and should avoid breaking the simple catalog and download flow that already works.

## Why This Exists

Parker currently supports basic OPDS browsing:

- users authenticate with Basic Auth
- libraries are listed from `/opds/`
- libraries list series
- series list issue acquisition entries
- issue downloads stream the original archive

Recent Android testing showed that small feed details matter:

- Moon+ Reader can browse Parker, download CBZ files, and read them after the OPDS compatibility updates.
- Moon+ Reader can download CBR files, but RAR5 CBR reading depends on the client, not Parker.
- Librera can browse Parker catalogs and display normal cover thumbnails, but tapping issue entries does not acquire/open them.
- Librera's enlarged cover preview appears to use its own fallback behavior even when the normal catalog cover renders.

This suggests Parker should keep strengthening standards-friendly OPDS behavior, but should not overfit to one client when the client appears to ignore or mishandle valid acquisition links.

## Current System Shape

The main OPDS implementation lives in:

- `app/routers/opds.py`
- `app/templates/opds/feed.xml`
- `app/api/opds_deps.py`
- `tests/api/test_opds.py`

Current feed hierarchy:

- `Library -> Series -> Issues`

Volumes are flattened in the series feed and issues are ordered by volume number, then issue number. This is valid OPDS and keeps reader navigation simple, but it means Parker does not currently expose a structured `Series -> Volume -> Issues` OPDS path.

Current compatibility choices:

- OPDS feeds use a realm-bearing Basic Auth challenge.
- navigation links are absolute.
- issue acquisition links use filename-bearing download URLs.
- each issue emits one acquisition link.
- OPDS cover links point at a JPEG-specific thumbnail endpoint.
- comic downloads serve the original archive without transcoding or repacking.

## Non-Goals

Out of scope for the first expansion pass:

- replacing Parker's web reader or API with OPDS
- changing library-owner file format policy
- converting RAR5 CBRs to another format automatically
- making OPDS behavior client-specific for Librera
- changing the existing `/opds/series/{series_id}` flat issue feed without an additive fallback
- implementing a dedicated third-party mobile app API contract

## Recommended Sequencing

### 1. Pagination

Add pagination to large OPDS feeds before adding many more feed types.

This should cover:

- library series feeds
- series issue feeds if needed
- future collection, reading list, smart list, and search feeds

The feed should expose standard OPDS/Atom navigation links such as `next`, `previous`, `first`, and `last` where practical.

Open questions:

- default page size
- whether page size should be configurable
- whether total counts are cheap enough to expose consistently

### 2. Additional Root Feeds

Expose Parker-native browsing surfaces from the OPDS root without disturbing the existing library path.

Candidate root entries:

- Libraries
- Continue Reading
- Recently Updated
- Recently Added
- Reading Lists
- Collections
- Smart Lists

The root feed should remain readable in simple clients. Avoid deep hierarchy unless the user selected an entry that naturally needs it.

### 3. Continue From Entries

Add optional synthetic entries at the top of relevant feeds when Parker has reading progress.

Examples:

- `Continue From #12`
- `Continue Reading: Batman #404`

These entries should be aliases to the underlying comic acquisition link, not separate content. The purpose is to help users avoid scrolling through long series or reading lists in clients with limited metadata support.

This should likely be configurable per user because it changes feed presentation.

### 4. OPDS Search

Investigate OpenSearch support for OPDS clients.

Parker already has a richer search system internally, but OPDS search should start small:

- query by title/series text
- return acquisition entries and/or navigation entries
- paginate results
- preserve age-rating and library access restrictions

Open questions:

- whether search should return series, issues, or both
- how to represent Parker's advanced saved-search rules in OPDS
- how many Android clients expose OpenSearch well

### 5. Structured Volume Browsing

Add volume-aware feeds without replacing the current flat series feed.

Recommended additive routes:

- `/opds/series/{series_id}` remains flat for compatibility
- `/opds/series/{series_id}/volumes` lists volumes
- `/opds/volumes/{volume_id}` lists issues for one volume

The series feed could include a `Browse Volumes` navigation entry if testing shows clients handle it cleanly.

### 6. Revocable OPDS Tokens

Investigate per-user OPDS tokens so users do not need to put their main Parker password into third-party reader apps.

The token should be:

- revocable
- optionally expiring
- scoped to OPDS/API access
- visible from the user dashboard

Possible authentication models:

- username plus OPDS token as password
- token embedded in an OPDS URL
- both, if clients differ in what they support

Security note: token-in-URL is convenient but can leak through logs, screenshots, and shared config exports. If implemented, Parker should make that tradeoff explicit.

### 7. Progress Encoded Titles

Consider optional progress markers in OPDS item titles for clients that do not show metadata-rich state.

This should be disabled by default or per-user configurable because it pollutes titles and may affect sorting.

Potential ASCII-friendly markers:

- `[ ]` no progress
- `[25%]` started
- `[50%]` halfway
- `[read]` complete

Avoid relying on Unicode symbols unless Parker can confirm common readers render them well.

### 8. OPDS-PS Page Streaming

Treat OPDS-PS as a later, larger feature.

It may help clients such as KOReader and some iOS readers stream pages without downloading the whole archive. It is less likely to help Android clients tested so far, since Moon+ Reader and Librera are OPDS-only rather than OPDS-PS-capable.

This work would likely touch:

- page image extraction
- page count metadata
- progress/resume semantics
- authentication for streamed image pages
- cache behavior
- client-specific validation

## Compatibility Principles

Prefer:

- additive routes over behavior changes
- one acquisition link per issue
- honest file types and filenames
- absolute URLs in OPDS feeds
- conventional image links for cover art
- documented client limitations

Avoid:

- duplicate acquisition links unless testing proves they are needed
- relabeling archive formats for reader compatibility
- automatic file conversion
- adding OPDS hierarchy that removes the existing flat path
- optimizing around Librera behavior without clear HTTP evidence

## Validation Targets

Known useful clients to test:

- Moon+ Reader on Android
- KOReader on Android or desktop
- Chunky on iOS, if an iOS tester is available
- Panels on iOS, if an iOS tester is available

Suggested smoke tests:

- add the OPDS catalog
- authenticate from a fresh client profile
- browse root, library, series, and issue entries
- view cover thumbnails
- download and read a CBZ
- download a CBR and confirm client-specific behavior
- test a large library feed once pagination exists
- test access restrictions for users with limited libraries or age ratings

