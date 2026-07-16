# Parker TODO

This file captures follow-up work that should not get lost between releases.

## Technical Debt

- Keep an eye on pull list detail page scale before it becomes a usability problem.
  Context: `app/templates/pull_lists/detail.html` currently renders a full list in one view, which is fine for normal pull-list sizes and likely more important to watch than the pull-list index page.
  Follow-up goal: If real users start building unusually large pull lists, consider pagination, filtering, or virtualization for list contents before the detail view becomes heavy to use.
  Guardrail: Treat this as a usage-driven optimization, not a default roadmap item, unless real list sizes or UX complaints justify it.

- Keep an eye on smart filter scale before the search load menu gets crowded.
  Context: Smart filters currently surface mainly in the dashboard management table and the shared "Load" dropdown on `app/templates/search.html`, where they sit alongside saved searches.
  Follow-up goal: If users start accumulating enough smart filters that discovery or loading becomes awkward, improve those surfaces before adding heavier API pagination.
  Candidate direction: Start with a bounded scroll area and/or lightweight client-side filtering in the search load menu, then revisit whether the dashboard table needs search, sorting, or pagination based on real usage.
  Guardrail: Treat this as a usage-driven UX refinement, not a release-blocking task, unless real list counts or complaints show the current UI is straining.

- Revisit frontend route-map generation in `app/core/utils.py`.
  Context: FastAPI `0.137.0` changed `router.routes` from a flat list into a tree of intermediate objects, and the release notes explicitly warn that code iterating `router.routes` directly will be affected.
  Current state: Parker ships a compatibility fix that walks the newer wrapped route structure and is covered by focused regression tests.
  Follow-up goal: Refactor the route-map helper to rely on the most stable/public FastAPI mechanism available instead of depending on wrapper internals like `original_router` and `include_context`.
  Candidate direction: Evaluate newer FastAPI route-context helpers such as `iter_route_contexts()` and confirm the best supported approach for nested routers and frontend route discovery.

- Expand the admin diagnostics support snapshot after it has seen a few real support incidents.
  Context: The current snapshot is intentionally lean and already covers startup status, runtime mode, database path/sizes, counts, configured-library samples, and the comics-path probe.
  Follow-up goal: Only add more fields if they repeatedly save support back-and-forth in real reports.
  Likely v2 additions:
  Recent scan job summary (last few jobs, status, timestamps, error text).
  Library detail summary (`last_scanned`, `is_scanning`, maybe a few more library rows than the current sample).
  Key request/runtime settings that commonly affect support cases (`base_url`, selected proxy-related settings, maybe a few safe env-derived values).
  Light filesystem health checks for storage/cache/cover directories.
  Guardrails: Avoid secrets, tokens, full env dumps, or overly large payloads.

- Revisit the startup diagnostics mismatch heuristic for true first-run versus rebuilt-storage scenarios.
  Context: The current warning logic is much more useful than before, but we lost the original reporter's environment after they rebuilt from scratch, so we no longer have a live repro to interrogate.
  Current risk: A fresh install with a valid comics mount and no configured libraries can still look superficially similar to a "wrong storage directory" situation.
  Follow-up goal: Reassess the signals used for `storage_mismatch_suspected` once we have another real-world report or a better synthetic repro, and tune the messaging so first-run onboarding is not mistaken for a broken upgrade.
