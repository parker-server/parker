# Archived Vertical-Scroll Compatibility Scope

Status: Draft

This note sketches a narrow approach for improving support for webcomic / webtoon-style material that has already been packaged into `.cbz` or `.cbr` archives.

The intent is not to add support for loose image folders or native online webcomic sources. The goal is only to improve the reading experience for unusually tall archived pages inside Parker's existing archive-based workflow.

## Why This Exists

Some users may have comics from gray-area or fan-archival sources where originally vertical-scroll material has been repackaged into standard comic archives.

Those files may still scan and open today, but the reader is currently optimized for page-turn reading:

- one or two pages at a time
- fixed viewport
- tap zones for next / previous
- progress tracked by page index
- optional spread logic for wide pages

That works well for conventional western issues and manga, but it is likely to be awkward for very tall pages or long-strip episodes.
It is also likely to be awkward for archived "slice" releases where adjacent image files together form a continuous vertical reading flow.

## Non-Goals

Out of scope for this work:

- support for loose image folders
- support for direct remote webcomic sources
- changes to library scanning beyond existing `.cbz` / `.cbr` support
- a general-purpose HTML/web article reader
- speculative metadata models for "webtoon" as a first-class format before we have sample files

## Working Assumption

The most realistic near-term target is:

- archived files that still contain sequential image assets
- users want to scroll vertically through the content instead of paging through it screen-by-screen
- some archives may contain unusually tall pages
- some archives may instead contain many mobile-sized image slices that reconstruct a vertical-scroll episode when stacked in order

We should validate this assumption against real sample archives before implementation.

### Updated Sample Finding

A real-world gray-area sample reported by the user:

- `Peter Parker Spider-Man - Queens Finest - Infinity Comic 001 (2026) (digital-mobile-Empire).cbz`

Observed behavior:

- the archive is chopped into separate sequential images
- some panels are intentionally cut across image boundaries and continue in the next image

Implication:

- not all archived vertical-scroll material will appear as one or a few extremely tall images
- some repackaged releases are better understood as "slice archives"
- Parker's main need is likely a stacked vertical reading mode for ordered archive images, not only special handling for extreme aspect ratios

## Current System Constraints

The current reader has several assumptions that are good for paged comics but likely hostile to vertical-scroll material.

### Reader Layout

The reader viewport is fixed and hides overflow:

- `body { overflow: hidden; }`
- `.reader-content { overflow: hidden; }`

This prevents natural vertical document scrolling.

Relevant file:

- `app/templates/reader.html`

### Navigation Model

Reader interaction is page-turn based:

- left/right tap zones
- swipe left/right navigation
- keyboard next / previous
- scrubber based on `page_count`
- `currentPage` as the main state anchor

Relevant file:

- `app/templates/reader.html`

### Double-Page / Spread Logic

The reader currently contains logic for:

- single-page mode
- double-page mode
- cover offset
- spread detection based on landscape images
- manga RTL vs western LTR

Most of that logic is either irrelevant or counterproductive for long-strip reading.

Relevant file:

- `app/templates/reader.html`

### Progress Model

Reading progress is page-based, not scroll-position-based:

- progress updates store `current_page`
- completion is based on `current_page >= total_pages - 1`
- activity logging records pages turned

Relevant files:

- `app/templates/reader.html`
- `app/services/reading_progress.py`

This may still be usable for archived vertical-scroll files if progress is mapped to the topmost visible image index rather than a pixel offset.
For slice archives, this means stored progress would effectively track the last reached image slice rather than a true comic "page," which is probably acceptable for an MVP.

### Image Processing

The image service optionally transcodes and resizes large images for data savings.

Current behavior uses a maximum dimension cap during WebP transcode. That may be too aggressive for extremely tall pages and could damage readability for archived webtoon-style content.
This risk may matter less for pre-sliced mobile releases, but still needs validation against real samples.

Relevant file:

- `app/services/images.py`

## Proposed Framing

We should avoid presenting this as broad "webcomics support."

Better framing:

- `Archived Vertical-Scroll Compatibility`
- `Tall Page Reader Improvements`
- `Long-Strip Archive Reading`

This keeps the feature grounded in Parker's actual supported input formats.

## Suggested MVP

The smallest useful implementation would likely be a new reader mode, not a rewrite of the default one.

### MVP User Story

As a user opening a `.cbz` or `.cbr` archive containing either very tall images or many sequential mobile-style slices, I can switch into a vertical-scroll reading mode that makes the content readable without adding support for loose images or new file formats.

### MVP Behaviors

- add a `scroll` reader mode alongside current paged modes
- render pages in a vertical column instead of a fixed page-turn viewport
- allow native vertical scrolling
- preserve existing archive-backed image loading
- treat archive image boundaries as rendering boundaries, not necessarily meaningful comic page boundaries
- disable double-page mode and spread pairing while in scroll mode
- keep LTR / RTL paging concepts out of scroll mode unless sample files prove a need
- update progress using the topmost visible page index or last meaningfully viewed page index
- keep completion based on reaching the last archived page

## Places Likely To Change

### `app/templates/reader.html`

Likely the main work area:

- new `readingMode` state such as `paged` vs `scroll`
- alternate layout for vertical flow
- conditional controls and shortcuts
- conditional hiding of spread-only settings
- scroll observer logic for progress updates
- adjusted mobile touch behavior

### `app/services/reading_progress.py`

May need small changes only if current page-based semantics prove insufficient.

Preferred approach:

- keep the persistence model unchanged if possible
- continue storing page index
- define scroll progress as "most recently reached page image"

This avoids schema churn.

### `app/services/images.py`

Needs review for data-saver behavior on extreme aspect ratios.

Open questions:

- should tall images skip the current max-dimension shrink path?
- should resize rules preserve width more aggressively for tall pages?
- should data-saver be disabled or adjusted in scroll mode?

### Tests

We will likely want focused coverage for:

- reader init with tall-page-friendly mode selection if auto-detection is added
- progress updates while scrolling
- completion when final archived page becomes visible
- data-saver behavior for extreme aspect ratios

Frontend reader behavior may need a mix of template-level regression tests and targeted service tests where possible.

## Auto-Detection vs Manual Toggle

Recommended initial approach: manual toggle.

Why:

- we do not yet know how repackaged files are structured in the wild
- some archives may mix standard pages and tall pages
- false positives would be annoying in the main reader

Possible future auto-detection signals:

- high proportion of pages exceeding a tall aspect-ratio threshold
- issue with a very small number of extremely tall pages
- ComicInfo / metadata hints such as `<Format>WebComic</Format>` or `<Format>Web Comic</Format>`, if real libraries show that those values are used consistently enough to trust
- issue metadata or filename hints such as `digital-mobile` or known release conventions, if those patterns prove reliable enough to justify using them
- metadata hints if any appear in real sample archives

For MVP, a user-controlled toggle is safer.

## Open Questions To Validate With Real Samples

- Are repackaged webcomics usually one huge image per episode, or many medium-height/mobile-height slices?
- Do users expect seamless continuous scrolling, or page-by-page vertical reading?
- Are sample files mostly JPEG, PNG, or mixed assets?
- How often are there decorative spacer images that should not affect progress too aggressively?
- How often do panels intentionally continue across adjacent image files?
- Does current archive page ordering already match intended reading order?
- Does data-saver resizing make text unreadable on tall images?
- How often do real libraries actually populate ComicInfo `<Format>` with values like `WebComic` or `Web Comic`, and how trustworthy are those tags in mixed collections?

## Recommendation

Do not commit this to a release until we have real sample files.

Once samples are available, the preferred path is:

1. verify that the content arrives inside normal `.cbz` / `.cbr` archives
2. inspect page dimensions and archive ordering
3. prototype a manual `scroll mode` in the existing reader
4. adjust image-transcode rules for tall-page readability if needed
5. only then decide whether this deserves release-note status

## Release Planning Note

This feels better as a targeted reader enhancement than as a headline format-support feature.

Possible release-note wording later, if validated:

- Added an optional vertical-scroll reader mode for tall-page content inside archived comic formats such as `.cbz` and `.cbr`.
