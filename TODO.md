# Parker TODO

This file captures follow-up work that should not get lost between releases.

## Technical Debt

- Revisit frontend route-map generation in `app/core/utils.py`.
  Context: FastAPI `0.137.0` changed `router.routes` from a flat list into a tree of intermediate objects, and the release notes explicitly warn that code iterating `router.routes` directly will be affected.
  Current state: Parker ships a compatibility fix that walks the newer wrapped route structure and is covered by focused regression tests.
  Follow-up goal: Refactor the route-map helper to rely on the most stable/public FastAPI mechanism available instead of depending on wrapper internals like `original_router` and `include_context`.
  Candidate direction: Evaluate newer FastAPI route-context helpers such as `iter_route_contexts()` and confirm the best supported approach for nested routers and frontend route discovery.
