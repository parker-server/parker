# Rail Component Architecture

Status: Proposed

This note captures a possible direction for organizing Parker's content rails. It is a reference for future work that includes a suggested incremental path, but it is not a commitment to schedule that work or to introduce a new frontend framework.

## Why Investigate This Now

Rails now appear in more than one part of the application:

- Home has many built-in rails with independent loading and layout behavior.
- Admin-defined featured rails are data-driven and appear alongside built-in Home rails.
- Series and volume detail pages render recommendation rails near their footers.
- Existing card partials, such as `partials/series_card.html` and `partials/volume_card.html`, already provide a reusable presentation layer for individual items.

The repeated rail heading and horizontal-scroll markup is a useful signal that the *rail shell* has become a UI concept worth naming and reusing. The goal is to reduce duplication and make new rail work more predictable without turning Parker into a page-builder or replacing Jinja and Alpine.

## Recommendation

Use a small component pattern built with standard Jinja macros, includes, and Alpine. Do not add React, Vue, or a third-party Jinja component package for this alone.

The key is to keep three responsibilities separate:

```text
Rail source/provider -> rail descriptor/layout data -> reusable rail UI shell
```

- A **rail source/provider** determines which items belong in a rail.
- A **rail descriptor** carries display and layout metadata.
- A **rail UI shell** renders shared structure, while item card partials render the actual content.

Keeping these separate avoids creating a single `Rail` abstraction that owns querying, layout policy, API serialization, and HTML rendering all at once.

## Jinja Component Pattern

Jinja macros and `caller()` blocks provide an effective component-like primitive without a dependency. A macro can standardize:

- section spacing
- a title row
- an optional action, such as `View All`
- the horizontal scroller and its Tailwind classes
- accessibility and data attributes shared by rails

The caller supplies the rail-specific item loop and card partial.

Illustrative shape:

```jinja
{% from "partials/rail.html" import rail %}

{% call rail(title_expr="lane.title") %}
  <template x-for="series in lane.items" :key="series.id">
    {% include "partials/series_card.html" %}
  </template>
{% endcall %}
```

The macro itself should remain deliberately modest. It should not fetch data, decide permissions, or assume that every rail contains series. It should accept enough options for ordinary variations, for example:

- static title or Alpine title binding
- optional icon and icon color class
- optional header action (link or button)
- narrow overrides for preserving existing spacing during the first extraction

Visibility stays with the caller's wrapper, and card sizing stays with the caller's item markup. See [Macro Signature](#macro-signature) for the proposed parameters.

Rails with genuinely unusual markup can keep their own template. Reuse should reduce obvious repetition, not force every future design into the same visual structure.

## Existing Structure and Immediate Opportunity

`app/templates/index.html` currently owns Home rail ordering, several rail-specific branches, content checks, and fetch behavior. The series and volume detail templates have closely matching recommendation-rail markup.

The most conservative first extraction is the shared visual shell used by those recommendation lanes, followed by the repeated Home heading/scroller structures where the markup truly matches. Existing `series_card.html` and `volume_card.html` partials stay responsible for their respective items.

This gives Parker a UI component boundary without changing data retrieval or making the current Home page's independent loading behavior more complicated.

### Current Duplication

As of this note, `index.html` contains roughly 13 copies of the section/title-row/scroller structure, and the series and volume detail recommendation lanes are near-identical copies of each other.

### Existing Variations to Validate

The copies contain variations that should be preserved during the first extraction, then evaluated as product decisions. Some may be intentional—for example, detail-page recommendations may be deliberately quieter than Home rails—so they should not automatically be normalized as part of a structural refactor.

| Element | Variant A | Variant B |
| --- | --- | --- |
| Scroller spacing | `space-x-4 pb-6` (most Home rails) | `space-x-6 pb-4` (`want_to_read`, `recently_added_series`, `recently_updated_series`, series/volume recommendations) |
| Comic card wrapper width | `w-40` (`resume`, `following_arrivals`, `up_next`) | `w-36` (`smart_lists`, `top_rated`, `top_parker_rated`) |
| Header action link size | `text-xs` (most rails) | `text-sm` (`recently_added_series`, `recently_updated_series`) |
| Title icon treatment | Lucide icon on most Home rails; smart-list rails can use a user-selected icon | No icon on series/volume recommendation lanes |

Before normalizing any of these differences, decide whether it is intentional. Repeated, intentional variations can become named macro options after the first extraction. Card widths should remain with the caller, alongside the chosen comic, series, or volume card partial, rather than becoming a rail-shell concern.

### Rails That Should Stay Hand-Written

Some rails do not fit the shared shell and should keep their own markup, at least initially:

- `pinned_libraries`: an outer heading containing nested per-library sub-rails with `h3` subheadings and their own actions.
- `smart_lists`: each list owns a per-item `x-data="smartRail(list)"` scope with its own loading state and a user-selected or default icon.

The macro is a rail shell with a required header, so these rails stay outside it. If reusing the shell without a header later proves worthwhile, add a `header=True` parameter and make the title rule under [Macro Signature](#macro-signature) conditional on it in the same change. Forcing these rails into the macro is not a goal.

### Macro Signature

A `show_binding` parameter is likely unnecessary. Home visibility is already handled by the outer `data-home-rail` wrapper (`isRailLoading` / `railHasContent`), and the detail-page lanes are wrapped by their own `x-show`. A smaller signature is probably sufficient:

```jinja
{% macro rail(title=None, title_expr=None, icon=None, icon_class=None,
              action_label=None, action_href_expr=None, action_click=None,
              action_class=None, scroller_class=None) %}
```

When `title_expr` is used, the macro must bind `x-text` to an inner `<span>` rather than to the `<h2>` itself. Alpine's `x-text` replaces all of an element's children, so binding it on the heading would remove the icon. The current promoted rail already follows this pattern; the recommendation lanes bind `x-text` on the `<h2>` only because they have no icon.

Exactly one of `title` or `title_expr` must be supplied. Supplying both, or neither, is an invalid call and raises a template error (see [Enforcing Macro Invariants](#enforcing-macro-invariants)); a rail with no title would render a header with no accessible name. The template check cannot catch a `title_expr` that evaluates to an empty string in the browser; that is a data concern for the caller.

The macro should have one agreed default scroller treatment that represents the current majority of rails (`space-x-4 pb-6`). `scroller_class` is a deliberately narrow escape hatch for a rail whose current spacing must be preserved, such as `space-x-6 pb-4`. `action_class` serves the same purpose for header actions, such as the `text-sm` links on the recently added and recently updated rails, and the Spin Again button, which currently lacks `transition-colors`.

Both overrides **replace** the corresponding default class string entirely; they are never appended to it. Appending would produce conflicting utilities (for example `text-xs text-sm`), and Tailwind resolves such conflicts by stylesheet order rather than by attribute order, so the result would be unpredictable to the call site. Replacement means an overriding call site repeats the full class string, including the scrollbar utilities, which is an acceptable cost for an escape hatch. If the same override recurs, introduce a narrow semantic option instead (for example `action_size="sm"`). This is preferable to introducing broad `home` or `detail` variants before the project has established that each surface has a stable, shared visual contract.

Passing a complete Tailwind class string should not become the macro's primary styling API. Once several rails share the same intentional override, promote it to a named, semantic option. A preset or variant is appropriate only when multiple style choices consistently belong together.

The header action's element is inferred from which parameter is supplied:

- `action_href_expr` renders an anchor with `:href` bound to the expression.
- `action_click` renders a `type="button"` button with `x-on:click` bound to the expression.
- Neither renders no action.
- Supplying both is an invalid call and raises a template error. The macro must not silently prefer one, because that would hide an authoring mistake and could disable an intended button action.
- `action_label` is required whenever `action_href_expr` or `action_click` is supplied, and is invalid without one of them. Either mismatch raises a template error rather than rendering an empty or inert action.

Inferring the element avoids a separate `action_kind` parameter that could disagree with the parameters actually supplied. This accounts for existing link actions such as `View All` and `Manage Following`, as well as the `Spin Again` button. Each additional Alpine-expression string parameter makes call sites harder to read, so new options should be added only when a second rail needs them.

### Enforcing Macro Invariants

Jinja has no built-in way to raise an error from a template, so Parker exposes a general-purpose `template_error(message)` global in `app/core/templates.py`, alongside the existing globals such as `url`, `routes`, and `get_system_setting`. It raises Jinja's `TemplateRuntimeError` with the given message. It is not rail-specific; any macro with mutually exclusive or required parameters can use it.

```jinja
{% if (title and title_expr) or not (title or title_expr) %}{{ template_error("rail(): pass exactly one of title or title_expr") }}{% endif %}
{% if action_href_expr and action_click %}{{ template_error("rail(): pass action_href_expr or action_click, not both") }}{% endif %}
{% if (action_href_expr or action_click) and not action_label %}{{ template_error("rail(): an action requires action_label") }}{% endif %}
{% if action_label and not (action_href_expr or action_click) %}{{ template_error("rail(): action_label requires action_href_expr or action_click") }}{% endif %}
```

Because the error is raised at render time, an invalid call site fails as soon as any test or page view renders that template, rather than shipping as a silently missing title or action. Add a focused test for the global itself, and render tests covering the supported title, link-action, and button-action paths of the rail macro.

## Backend Shape When Needed

No backend hierarchy is required merely to extract a template macro. If rails continue to expand across pages, however, the backend can gradually adopt a normalized descriptor returned to the UI.

Useful descriptor fields could include:

```python
@dataclass(frozen=True)
class RailDescriptor:
    key: str
    kind: str
    title: str
    item_kind: str
    visible: bool
    reorderable: bool
    action_url: str | None
    action_label: str | None
```

Possible values:

- `kind`: `built_in`, `promoted`, `recommendation`, or another stable source category
- `item_kind`: `series`, `volume`, or `comic`, which lets the UI select the appropriate card treatment

`visible` and `reorderable` are shown above for completeness, but they are Home layout concerns rather than intrinsic properties of a rail: a recommendation lane on a series page has no meaningful `reorderable`, and visibility is per-user Home placement state. If a descriptor is introduced, split these into a separate Home placement shape (for example `key`, `position`, `visible`) that references the descriptor, so the descriptor stays reusable on surfaces other than Home. This does not block the UI-shell work.

Only when several sources need a common lifecycle should Parker introduce a provider contract, such as:

```python
class RailProvider(Protocol):
    def build_descriptor(self, context: RailContext) -> RailDescriptor: ...
    def load_items(self, context: RailContext) -> list[RailItem]: ...
```

This would fit sources with distinct retrieval strategies—built-in Home queries, promoted search-query rails, and recommendations—without making the templates responsible for those differences.

Provider code must continue to enforce existing library-access, age-rating, and other query constraints. A rail's visibility is presentation state, never a security boundary.

## Suggested Incremental Path

1. Record each existing visual variation and decide only which ones must be preserved for a no-appearance-change extraction. Do not normalize styles yet.
2. ~~Add the general-purpose `template_error` global with a focused test.~~ Done: see `app/core/templates.py` and `tests/core/test_templates.py`.
3. Extract a `partials/rail.html` Jinja macro with the majority scroller style as its default and `scroller_class` / `action_class` for preserved exceptions. Follow the existing macro conventions in `partials/admin_header.html`. Convert the series and volume recommendation lanes together with two or three standard Home rails (for example `trending` and `popular`), so the default is validated against the rails it was chosen for rather than only against the recommendation lanes, which use the minority spacing.
4. Implement the inferred link/button action contract so links and buttons remain semantically correct, and convert a rail of each kind (for example `recently_added_series` and `random_gems`).
5. Reuse the macro for the remaining matching Home rails while retaining existing card partials and Alpine fetch functions. Keep `data-home-rail` on the outer wrapper so existing Playwright selectors continue to work.
6. Evaluate whether the Home-specific key-to-fetcher and key-to-template branching is still easy to maintain. Once each branch is a short macro call, an explicit `x-if` chain is likely still the clearest option; a JavaScript-side descriptor table is complicated by icons being rendered server-side through `lucide_icon()`.
7. Promote repeated, intentional visual overrides to named semantic options only after the call sites establish a real pattern.
8. If featured rails need to appear on series or volume pages, or multiple new rail types emerge, define the shared `RailDescriptor` and optional provider contract.
9. Consider a consolidated feed endpoint only if the independent Home requests become a performance or coordination problem.

## Non-Goals

This direction does not imply:

- migrating Parker to React, Vue, or another frontend framework
- adding a generic page-builder system
- replacing all rail APIs with one endpoint now
- forcing all rails to return the same item shape
- replacing existing card partials
- making hidden rails an access-control or privacy mechanism

## Decision Rule

Adopt the Jinja rail shell now if it removes repeated markup cleanly in two or more locations. Treat the backend provider interface as a later extraction: introduce it only once new rail sources repeatedly need the same descriptor-and-loading contract.

That sequencing keeps the change small, consistent with the current FastAPI, Jinja, Alpine, and Tailwind stack, while leaving a clear path for rail growth.
