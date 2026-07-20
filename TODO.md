# Parker TODO

This file captures follow-up work that should not get lost between releases.

## Technical Debt

- Explore container-level Continue behavior for reader contexts.
  Context: Reader navigation currently preserves launch-time context for reading lists, collections, stacks, series, volumes, and story arcs, but persisted progress is comic-level. Home rails resume the comic and page only, while container "Start reading" actions begin at the first item instead of resuming the user's last position within that container.
  Follow-up goal: Decide whether container pages should offer a context-aware Continue action that finds the best in-progress or next unread item within that specific container and launches the reader with that container context.
  Candidate direction: Treat this as a generic reader-session/container-resume feature rather than a story-arc-specific workaround. Reading lists, collections, stacks, and possibly scoped story arcs should share the same design if the feature is worth building.
  Guardrail: Avoid changing global home-rail resume behavior unless there is a broader product decision to persist last reader launch context; the current comic/page-only resume is simple and predictable.

- Keep an eye on stack list detail page scale before it becomes a usability problem.
  Context: `app/templates/pull_lists/detail.html` currently renders a full list in one view, which is fine for normal stack sizes and likely more important to watch than the stack index page.
  Follow-up goal: If real users start building unusually large stacks, consider pagination, filtering, or virtualization for list contents before the detail view becomes heavy to use.
  Guardrail: Treat this as a usage-driven optimization, not a default roadmap item, unless real list sizes or UX complaints justify it.

- Keep an eye on smart filter scale before the search load menu gets crowded.
  Context: Smart filters currently surface mainly in the dashboard management table and the shared "Load" dropdown on `app/templates/search.html`, where they sit alongside saved searches.
  Follow-up goal: If users start accumulating enough smart filters that discovery or loading becomes awkward, improve those surfaces before adding heavier API pagination.
  Candidate direction: Start with a bounded scroll area and/or lightweight client-side filtering in the search load menu, then revisit whether the dashboard table needs search, sorting, or pagination based on real usage.
  Guardrail: Treat this as a usage-driven UX refinement, not a release-blocking task, unless real list counts or complaints show the current UI is straining.

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

- Decide the long-term policy for inaccessible followed volumes.
  Context: `user_volume_follows` rows currently remain persisted even if a user's library access or age-rating settings later hide that volume from all user-facing follow surfaces.
  Current behavior: The follow is filtered out of the `Following` page, the `New from Following` home rail, and direct volume access checks, but the row is not pruned automatically.
  Follow-up goal: Confirm whether Parker should keep this hidden-and-persisted behavior, surface inaccessible follows in a disabled state, or automatically prune them after some explicit rule.

- Design support for multi-root libraries.
  Context: Parker currently assumes one configured filesystem root per library, but some users may want a single logical library to aggregate comics from multiple folder locations across disks, shares, or staged/import storage.
  Scope risk: This is likely a medium-to-large architectural change rather than a simple admin-form enhancement because it would affect the data model, scanning, file watching, duplicate handling, library stats, and background-job fairness assumptions.
  Follow-up goal: Define whether Parker should support multiple roots under one library, how root-level failures and duplicate files should be handled, and what scanner/watcher changes would be required before implementation work begins.
  Design note: `docs/multi-root-library-scope.md`
