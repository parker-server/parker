# Reader Annotation Overlays Scope

Status: Planning / foundational branch

This note captures the product and technical direction discussed before the `feature/reader-overlay-layer` branch started. The immediate goal is not to ship full annotations in one pass. The goal is to establish a page-scoped SVG overlay foundation that can support a future annotation system without tying the reader to one annotation implementation.

## Why This Exists

Parker already has reader bookmarks, bookmark detours, context-aware reader navigation, paged and long-view reading modes, double-page spreads, manga/RTL behavior, and per-comic reader overrides. Those features make page-specific reader state useful, but they also mean visual annotations must be designed carefully.

A simple `notes` field on bookmarks would be useful, but it would mostly be `Bookmarks 2.0`: save this page and optionally explain why. That is not the same thing as a real annotation system.

A robust annotation system should let a user attach commentary or visual markup to a comic page or, later, a page region/panel, whether or not that page is bookmarked.

## Product Framing

Recommended concept split:

- **Bookmark:** I want to come back to this page.
- **Annotation:** I have something to say about this page or part of this page.
- **Overlay:** The reader infrastructure that renders page-relative visual layers above comic images.

This keeps annotations separate from bookmarks while still allowing the future annotation implementation to reuse bookmark patterns such as user ownership, comic access checks, age-rating checks, incognito restrictions, page validation, modal/panel behavior, and list/create/update/delete API shapes.

## Core Decisions

### Use SVG Exclusively

Use SVG for visual overlays instead of `<canvas>`.

Reasons:

- SVG provides native primitives for annotation shapes: `path`, `rect`, `circle`, `line`, `polyline`, `g`, and `title`.
- Saved annotations remain individually selectable and editable in the DOM.
- Hit testing, hover/selected states, labels, and accessibility affordances are easier than with canvas.
- SVG scales cleanly across fit modes and screen sizes when driven by normalized coordinates.
- Parker does not need heavy bitmap-style drawing in the first pass.

Avoid exotic SVG features for the first version. Prefer simple, broadly supported primitives and avoid filters, masks, embedded HTML, `foreignObject`, and complex text layout until there is a concrete need.

### Do Not Modify Comic Archives

Annotations should be stored as Parker-side user data, not written back into CBZ/CBR archives or `ComicInfo.xml`.

Parker's filesystem/archive content remains the source of truth for comic metadata and page images. User-specific annotation state belongs in Parker's database, similar to reading progress and bookmarks.

Future export/import can use a sidecar JSON format, but archive mutation should stay out of scope.

### Page-Scoped Overlays, Not One Global Overlay

Each rendered comic page should own its own SVG overlay layer.

Preferred structure:

```html
<div class="reader-page-shell" data-page-index="12">
  <img class="reader-page-image" src="...">
  <svg
    class="reader-overlay-layer"
    data-overlay-page-index="12"
    viewBox="0 0 1 1"
    preserveAspectRatio="none">
  </svg>
</div>
```

This is important because Parker can render one page, two pages, or many scrolled pages. A per-page overlay keeps all coordinates relative to the source page image instead of the reader viewport or spread.

### Normalized Coordinates

Store and render overlay geometry in normalized page coordinates, not screen pixels.

Examples:

```json
{
  "type": "rect",
  "page_index": 12,
  "x": 0.18,
  "y": 0.32,
  "width": 0.25,
  "height": 0.12
}
```

```json
{
  "type": "path",
  "page_index": 12,
  "points": [
    [0.12, 0.20],
    [0.14, 0.23],
    [0.17, 0.25]
  ],
  "stroke_width": 0.004
}
```

The overlay system should provide shared helpers for converting browser pointer coordinates into normalized page coordinates. Annotation tools should not each reinvent coordinate mapping.

## Future Overlay System Possibilities

The overlay system should be generic enough to support more than annotations, but these should be treated as possible future consumers rather than committed roadmap items.

| Overlay Type | Purpose | Notes |
| --- | --- | --- |
| **Annotations** | Pins, boxes, freehand paths, and notes | The first real consumer and the reason for the foundational branch. |
| **Reading focus mask** | Dim everything except a panel or region | Could support distraction-free guided reading or accessibility experiments. |
| **Panel guide** | Show manually marked panel order | Useful if Parker ever explores guided panel-by-panel reading without OCR. |
| **Admin/debug overlay** | Show image bounds, page index, aspect ratio, detected landscape pages, or coordinate readouts | Useful during development and for diagnosing reader layout issues. |
| **Search/OCR results** | Highlight text regions from future OCR/search work | Explicitly out of scope now, but page overlays would be the natural rendering layer. |
| **Presentation mode** | Temporary laser-pointer-style marks during screen sharing | Could be session-only and non-persisted. |
| **Page issue markers** | Flag corrupt areas, bad crops, missing pages, or other page-level quality concerns | Could support future admin cleanup/reporting workflows. |

The practical near-term secondary consumer is the admin/debug overlay. It can validate page bounds, normalized coordinates, and pointer mapping before persisted annotation tools exist.

## Reader Mode Implications

### Single-Page Mode

This is the simplest case: one rendered page image and one overlay layer for the current page.

### Double-Page Mode

Each displayed page must keep its own overlay layer. Do not render one overlay across the full spread, because each page has a separate coordinate space.

Manga/RTL mode may change display order and navigation direction, but annotation coordinates must still belong to the source page image.

### Long View / Scroll Mode

Each scroll-rendered page should have its own shell and overlay. The overlay should scroll naturally with its page.

This mode creates the biggest interaction challenge on touch devices because drag gestures can mean either scroll the reader or draw/edit an annotation.

## Interaction Model

The reader should have a clear distinction between read mode and annotation/overlay interaction mode.

In read mode:

- overlays can be visible
- annotations can be passive/selectable if safe
- normal reader taps, swipes, keyboard shortcuts, and scrolling should continue to work

In annotation mode:

- overlay layers can capture pointer events
- drawing and shape placement are enabled
- reader navigation gestures should be suppressed or reduced while interacting
- an explicit Done/Exit action should return to reading

This is especially important on mobile, where tap, swipe, drag, and scroll gestures overlap heavily.

## Form Factor Considerations

Desktop is the easiest target because mouse input is precise, hover states are available, and keyboard shortcuts can support power-user flows.

Tablet and stylus should be a strong target for drawing annotations, but touch interactions need large controls and forgiving hit areas.

Phone support should be cautious. Basic viewing, pin placement, rectangle placement, and annotation editing are reasonable. Full freehand drawing in long view on a phone may be frustrating and can be deferred.

Recommended support level for early iterations:

| Form Factor | Support Level |
| --- | --- |
| Desktop mouse | Full support |
| Tablet touch/stylus | Good support |
| Phone | Basic support |
| Phone long-view freehand drawing | Defer or treat as experimental |

## Likely Annotation Feature Levels

### Level 1: Page Text Annotations

Simple page-attached notes. Useful, but visually close to bookmark notes if implemented alone.

### Level 2: Pins and Rectangles

A strong first visual annotation release. Users can point to a spot or draw a rectangular region and attach optional title/body text.

This feels meaningfully different from bookmarks without taking on the full complexity of freehand drawing.

### Level 3: Freehand SVG Paths

A pen tool that records pointer movement as normalized points and renders it as SVG paths.

This requires stroke simplification, undo/cancel behavior, pointer capture, mobile gesture handling, and careful interaction design.

### Level 4: OCR/Text Highlighting

Out of scope for now. Comics are image-based, so true text highlighting would require OCR and introduces accuracy, performance, language, and storage concerns.

## Proposed Code Separation

Use three reader-side JavaScript files:

- `static/js/reader.js`
- `static/js/reader-overlays.js`
- `static/js/reader-annotations.js`

Responsibilities:

### `reader.js`

Owns the existing reader state, navigation, progress, bookmarks, settings, launch context, and mode transitions.

It should integrate with overlays at a high level only:

- initialize overlay state
- expose current page/page metadata as needed
- ask overlays whether interactive mode is active
- suppress reader gestures when overlay interactions are active

### `reader-overlays.js`

Owns generic overlay infrastructure:

- overlay state factory
- active overlay mode/tool state
- page-relative coordinate mapping
- pointer routing
- renderer registry
- debug overlay support
- helpers for grouping overlay items by page

It should not know Parker annotation persistence details.

### `reader-annotations.js`

Owns the annotation domain and tools:

- annotation item shape conventions
- annotation renderers
- select/pin/rectangle/pen tools
- future API integration
- future annotation panel interactions

The foundational branch can include a placeholder/stub file so the separation exists from the start, even if persisted annotations are not implemented yet.

## Suggested Overlay Item Shape

Generic overlay item:

```json
{
  "id": "annotation-123",
  "type": "annotation",
  "page_index": 12,
  "shape": {
    "kind": "rect",
    "x": 0.2,
    "y": 0.3,
    "width": 0.25,
    "height": 0.12
  }
}
```

The generic overlay system should care about `type` and `page_index`. The type-specific renderer should care about annotation details.

## Future Server-Side Annotation Model

A future annotation model could look like:

```python
class Annotation(Base):
    id
    user_id
    comic_id
    page_index
    kind          # note, pin, rectangle, freehand
    title
    body
    color
    anchor_json   # normalized position/shape/strokes
    created_at
    updated_at
```

Candidate API shape:

```text
GET    /api/annotations/comic/{comic_id}
GET    /api/annotations/comic/{comic_id}/page/{page_index}
POST   /api/annotations/comic/{comic_id}
PATCH  /api/annotations/{annotation_id}
DELETE /api/annotations/{annotation_id}
```

This is not part of the foundational overlay branch unless deliberately expanded.

## Initial Branch Scope

`feature/reader-overlay-layer` should focus on the backbone:

- add `static/js/reader-overlays.js`
- add `static/js/reader-annotations.js` as a future annotation integration point
- add per-page SVG overlay layer partials
- wire overlay layers into paged mode and long-view mode
- load overlay scripts before `reader.js`
- initialize generic overlay state from the reader
- add normalized coordinate mapping helpers
- add an optional debug overlay to validate alignment and pointer mapping
- suppress or short-circuit reader gestures while overlays are in interactive mode

No database annotation model, annotation API, or persisted annotation UI is required for this first pass.

## Non-Goals For The Foundational Branch

Out of scope:

- adding a real `Annotation` database model
- adding annotation API endpoints
- saving annotations
- modifying bookmarks
- adding OCR or text selection
- writing annotations into comic archives
- building a generalized plugin marketplace for reader overlays
- fully solving mobile freehand drawing UX
- adding sharing/public annotation behavior

## Risks And Guardrails

### Coordinate Mapping

This is the most important technical risk. Pointer coordinates must be mapped to the actual rendered image bounds, not just the container or viewport.

The implementation should account for:

- image bounding boxes
- letterboxing from fit modes
- scroll position
- double-page layouts
- mobile viewport sizes
- device pixel ratio only where relevant

### Reader Gesture Conflicts

Annotation mode must not fight with page taps, swipe navigation, long-view scrolling, keyboard shortcuts, or bookmark/goto/settings modals.

### Over-Abstraction

The overlay system should be generic enough to support annotations and debug overlays, but it should not become a large plugin framework before there are multiple real consumers.

### Accessibility And Controls

Future annotation controls should avoid hover-only behavior and should provide explicit mobile-friendly mode switching. SVG elements should be selectable and labelable where appropriate.

## Recommended Implementation Phases

1. **Overlay infrastructure**: per-page SVG layers, coordinate mapping, debug renderer, mode flag.
2. **Passive visual annotations**: render mock/stub annotation items from `reader-annotations.js` without persistence.
3. **Persisted page/pin/rectangle annotations**: add backend model/API and reader UI.
4. **Freehand SVG path annotations**: add pen tool, stroke simplification, undo/cancel, and edit/delete behavior.
5. **Mobile polish**: bottom-sheet controls, larger handles, touch-specific gesture refinements.

## Naming

Foundational branch:

```text
feature/reader-overlay-layer
```

Future feature branch candidates:

```text
feature/reader-annotations
feature/page-annotations
feature/annotation-drawing-tools
```
