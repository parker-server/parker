# Parker Comic Server

Parker is a self‑hosted media server for comic books (CBZ/CBR). It follows a **“Filesystem is Truth”** philosophy, parsing metadata directly from `ComicInfo.xml` inside archives. Parker is currently at **Version 0.1.44 (Stable)**.

https://github.com/parker-server/parker/wiki/Getting-Started

---

## ✨ Features

- **Library Management**
  - Hierarchy: `Library → Series → Volume → Comic`
  - Rich metadata (credits, tags, page counts, colors)
  - Admin folder browser for selecting server-side library paths under `COMICS_PATH`
  - Multi-root libraries with explicit add, disable, relocate, and remove controls
  - Reading Lists, Collections, Story Arcs, Stacks, Smart Lists, Cover Murals
  - CBL-derived reading lists from uploaded files, direct HTTPS imports, and the DieselTech catalog
  - Volume-level `Following` for future issue tracking by run
  - Optional single-volume series shortcut to open the volume detail page directly
  - Optional volume dividers in the flat Series Issues tab to make new runs easy to spot
  - Paginated Details tabs for large series and volume metadata sets
  - Alphabet jump navigation for large library detail pages

- **User System**
  - Multi‑library access with row‑level security
  - Age-rating-aware access control
  - Comic file downloads from the issue page and OPDS, controlled by a site-wide **Allow Comic Downloads** setting plus a per-user download permission (admins can always download)
  - Admin-enforced password changes for accounts created or reset with temporary passwords
  - Anonymous Social Insights participation is enabled for new users by default, with an account-level opt-out
  - Avatar uploads
  - Hybrid authentication (JWT + secure cookies)

- **Reader**
  - Context‑aware navigation (series, volume, lists, story arcs)
  - Context-aware Continue actions for series, volumes, reading lists, collections, and stacks
  - Manga mode (RTL), responsive double‑page spreads, `Long View`
  - Per-book reader overrides for view mode, double-page, and reading direction
  - Incognito reading sessions that avoid persisting per-book overrides
  - Zero‑latency engine with preloading and swipe navigation
  - Separate per-comic bookmarks with detour-safe resume handling
  - Smart close behavior that returns readers to the page they launched from
  - Desktop keyboard shortcut reference
  - Image inspection tools, including a rectangular magnifier

- **Discovery**
  - Netflix‑style home page with content rails
  - Anonymous aggregate social insights such as reader counts and `Popular with Others`
  - `Continue Reading`, `Jump Back In`, `Trending`, and `New from Following`
  - Pinned library rails for recently updated series from favorite libraries
  - Per-user Home rail customization for reordering or hiding discovery rails
  - Admin-scheduled Featured Rails backed by Advanced Search, shown as series discovery cards with matching-issue handoffs and per-user hide controls
  - Library Timeline for character and team histories generated from embedded metadata
  - Dedicated person pages for creators with role-specific series rails and search handoffs
  - Recommendations by creator or metadata
  - Random gems, newly added series, and recently updated series with recency-aware volume covers and paginated series browsers

- **Reports Dashboard**
  - Missing Issues
  - Storage Analysis
  - Duplicate Detector
  - Metadata Health
  - Corrupt / Low Page Count

- **Administration**
  - About and admin dashboard surfaces can flag when a newer tagged Parker version is available
  - Kavita migration preview stages a database once in shared cache storage, summarizes new and matched users plus importable progress, requires review before migration runs, and cleans expired staging files at startup
  - Docker startup compacts the SQLite database when at least a quarter of the file (and at least 50 MB) is unused, skipping with a log warning and retrying next start if disk space is short

- **Scanning**
  - Background ScanManager with priority queues and separate lanes, so short enrichment jobs (like review lookups) never wait behind library scans
  - Admin job history includes a live active queue for running and pending work, with review lookups shown on request
  - Configurable job history retention for completed and failed background jobs
  - Global cleanup repairs case-only duplicate character, team, location, and genre tags
  - Timestamp bubbling for updated series
  - Physical page count validation
  - ⚡ Parallel Thumbnail Generation (Optional)
    - Multiprocessing pipeline for thumbnails
    - Distributes image decoding, resizing, WebP encoding, and palette extraction across CPU cores
    - Dedicated writer process batches SQLite commits safely
    - On an 8‑core i7 6th gen., 3,541 comics dropped from 13m40s → 1m58s
    - Fully opt‑in via Settings; Auto = use 50% of available cores

- **Visuals**
  - Dynamic backgrounds from cover colors
  - Configurable login backgrounds with curated static covers, cover context, and solid-color previews
  - Cover Browser gallery mode with lazy-loaded manifests for very large runs
  - Cover Murals for arranging selected covers into compact source-size composite images with optional spacing, horizontal/vertical overlap, overlap stacking, and export scaling

- **Format Support**
  - Native archive support for `CBZ` and `CBR`
  - Backend image-pipeline compatibility for `AVIF`
  - Experimental backend support for `JPEG XL` (`JXL`), with browser-dependent native reading support

- **Enrichment**
  - Auto‑populated event descriptions
  - Reading time estimates
  - Opt-in external critic review summaries from ComicBookRoundup, cached lazily when an issue is viewed and fetched through the persisted job queue with rate limiting

- **Search**
  - Advanced rule‑based search builder
  - Saved searches → Smart Lists
  - Secure autocomplete

- **OPDS Support**
  - OPDS 1.2 compliant feeds
  - Paginated library and series feeds
  - Root shortcuts for Continue Reading, newly added series, and recently updated series
  - Collection and reading list feeds
  - Optional volume browsing alongside the flat series issue feed
  - Dublin Core metadata
  - Revocable per-user OPDS keys for external readers
  - JPEG cover thumbnails for reader compatibility
  - Android reader compatibility improvements
  - Account settings integration for reader key management

- **WebP Transcoding**
  - On‑the‑fly conversion for bandwidth savings
  - Smart resizing and thresholds
  - Per‑device opt‑in

---

## 🛠 Tech Stack

- **Backend:** Python 3.10+, FastAPI, SQLAlchemy, Alembic, APScheduler, Watchdog
- **Frontend:** Jinja2, Alpine.js, TailwindCSS (CDN)
- **Image Processing:** Pillow, Color Thief
- **Deployment:** Docker / Docker Compose
- **Database:** SQLite (WAL mode) with FTS5

---

## 🔒 Architecture Highlights

- **Row‑Level Security:** Users restricted to accessible libraries
- **Dependency Injection:** Security enforced via `ComicDep`, `SeriesDep`, `VolumeDep`
- **Context‑Aware Reader:** Strategy pattern for next/prev navigation
- **Soft Landing Errors:** Inline UI errors instead of hard redirects
- **Hybrid Settings:** Environment variables + DB runtime preferences

---

## 📊 Data Model

- Libraries, Series, Volumes, Comics
- Collections, Reading Lists, Stacks, Smart Lists, Cover Murals
- User access control
- Batch operations (mark read/unread, add to lists)

---

## 🚀 Getting Started

https://github.com/parker-server/parker/wiki/Getting-Started

### Local Python Environments

Parker now uses pinned dependency versions in `requirements.txt` and
`requirements-dev.txt` so local machines, Docker builds, and CI all resolve the
same package set.

When setting up a new machine or refreshing an existing virtualenv, recreate the
environment from the pinned files instead of reusing older installed packages.

Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
```

If behavior differs between machines, verify both are using a freshly created
virtualenv from these pinned requirements.

### Library Browser Root

The admin folder browser is scoped to `COMICS_PATH`. Docker Compose sets this to
`/comics` by default. For local Python installs, set `COMICS_PATH` in `.env` to
the local folder Parker should browse from, such as `D:\Comics` or
`C:\Users\you\Comics`.

### Docker Port Configuration

The Docker image listens on port `8000` inside the container. To change the host
port exposed by Docker Compose, set `PARKER_PORT` in `.env`; for example,
`PARKER_PORT=9000` exposes Parker at host port `9000` while the container still
uses port `8000` internally.

### Secret Key Configuration

Parker requires `SECRET_KEY` to be set before startup. The value signs login
tokens, so it must be unique per server and kept private. Generate one with:

```bash
openssl rand -hex 32
```

Put the generated value in `.env` as `SECRET_KEY=<generated value>`. Parker will
refuse to start if the key is empty or still uses a known placeholder.

### Initial Admin Bootstrap

On a new database, Parker creates the first administrator from bootstrap
environment variables instead of using a shared default password.

Set `INITIAL_ADMIN_PASSWORD` before first startup. It must be at least 8
characters and should be unique to this server. `INITIAL_ADMIN_USERNAME` is
optional and defaults to `admin`.

```env
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=<your initial admin password>
```

After an active administrator exists, Parker ignores these bootstrap values so
upgrades do not reset or overwrite existing accounts.

### OPDS Reader Compatibility

Parker's OPDS feed serves original comic archives and preserves their file type.
Users can connect reader apps with their Parker username and a revocable OPDS
key created from Account Settings when OPDS is enabled by an administrator.
Account passwords still work for legacy setups, but OPDS keys can be revoked
without changing the main Parker password.
Reader support varies by app: some Android readers can download a CBR but fail
to open it if the archive uses RAR5 compression. In that case, use a reader with
RAR5 support or convert the file to CBZ/RAR4 at the library owner's discretion.

OPDS readers open comics by downloading them, so turning off **Allow Comic
Downloads** in Settings, or a user's download permission in Users, also stops
those users from reading through OPDS. Their feeds still list comics but leave
out the download links.

Known client behavior:

- Moon+ Reader can browse Parker OPDS catalogs and read downloaded CBZ files.
  CBR support depends on the archive's RAR version.
- Librera can browse Parker OPDS catalogs, but may not acquire/open issues when
  tapping comic entries.

### Cover Audit Tool

Parker picks each comic's cover from its archive's page filenames. To find
archives where that pick could be wrong and deserves a manual look, run the cover audit inside the
container:

```bash
docker compose exec parker python -m app.services.cover_audit
```

It reads every `.cbz`/`.cbr` under `COMICS_PATH` (or the folders you pass as
arguments) without changing anything, and writes the flagged archives to
`storage/cover_audit.csv` (`./data/cover_audit.csv` on the Docker host). Each row
lists the chosen cover, the first page by plain filename order, why the cover
was chosen, and one or more flags:

- `reordered`: the cover is not the first page by plain filename order
- `multiple_cover_candidates`: more than one page looks like a cover
- `cover_in_stray_folder`: the cover sits in a tiny folder apart from the rest of the pages
- `no_pages` / `unreadable`: the archive has no images or could not be opened

To check which covers an update changes, save a snapshot of every cover
before updating Parker, then compare against it afterwards:

```bash
docker compose exec parker python -m app.services.cover_audit --snapshot storage/covers_before.csv
```

```bash
docker compose exec parker python -m app.services.cover_audit --compare storage/covers_before.csv
```

The compare run writes `storage/cover_changes.csv` with each archive whose cover
changed, showing the cover before and after. Archives added or removed between
the two runs are not listed.

To test cover sorting changes without access to the library, save every
archive's file list once (filenames only, about 110 bytes per archive, gzipped):

```bash
docker compose exec parker python -m app.services.cover_audit --save-pages storage/pages.jsonl.gz --snapshot storage/covers.csv
```

On any machine with the code, `--replay` then runs the audit on those saved
file lists in seconds, without opening archives. Add `--compare` to list every
archive whose cover a change would move:

```bash
python -m app.services.cover_audit --replay storage/pages.jsonl.gz --compare storage/covers.csv
```

To check flagged covers by eye, build a contact sheet from a finished report.
This doesn't rerun the audit; it only reads a few pages from each flagged archive:

```bash
docker compose exec parker python -m app.services.cover_audit --contact-sheet storage/cover_audit.csv
```

Open `./data/cover_audit_sheet/index.html` on the Docker host in a browser. Each
flagged archive shows its chosen cover (outlined) next to the pages it might
have picked instead, with the most likely wrong picks first. Filter by flag,
tick **Wrong cover** on bad picks (ticks are remembered in that browser), then
use **Copy wrong covers** to get the list of archives and covers to report.

The contact sheet can also show a `cover_changes.csv` from `--compare`, with each
new cover next to the previous one, so you can check what a sorting change moved
before regenerating thumbnails:

```bash
docker compose exec parker python -m app.services.cover_audit --contact-sheet storage/cover_changes.csv
```

That sheet is written to `storage/cover_changes_sheet/`.


## 📌 Roadmap
- Improve Documentation
- Expanded OPDS support
- Localization
- Enhanced WebP transcoding pipeline (JXL, AVIF to WebP)
- Additional unit test coverage
- Migration tooling improvements
- Light metadata editing with file writeback (Tentative)

## 🤝 Contributing
Parker is early‑stage but stable. Contributions are welcome!
Please check the issues list for open tasks, or propose new features via pull requests.

## 📜 License
MIT License
