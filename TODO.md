# Parker TODO

This file captures follow-up work that should not get lost between releases.

## Technical Debt

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
