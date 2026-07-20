# Parker Comic Server

Parker is a self‑hosted media server for comic books (CBZ/CBR). It follows a **“Filesystem is Truth”** philosophy, parsing metadata directly from `ComicInfo.xml` inside archives. Parker is currently at **Version 0.1.23 (Stable)**.

https://github.com/parker-server/parker/wiki/Getting-Started

---

## ✨ Features

- **Library Management**
  - Hierarchy: `Library → Series → Volume → Comic`
  - Rich metadata (credits, tags, page counts, colors)
  - Reading Lists, Collections, Story Arcs, Stacks, Smart Lists
  - Volume-level `Following` for future issue tracking by run
  - Optional single-volume series shortcut to open the volume detail page directly

- **User System**
  - Multi‑library access with row‑level security
  - Age-rating-aware access control
  - Anonymous Social Insights participation is enabled for new users by default, with an account-level opt-out
  - Avatar uploads
  - Hybrid authentication (JWT + secure cookies)

- **Reader**
  - Context‑aware navigation (series, volume, lists, story arcs)
  - Manga mode (RTL), double‑page spreads, `Long View`
  - Per-book reader overrides for view mode, double-page, and reading direction
  - Incognito reading sessions that avoid persisting per-book overrides
  - Zero‑latency engine with preloading and swipe navigation
  - Separate per-comic bookmarks with detour-safe resume handling
  - Smart close behavior that returns readers to the page they launched from
  - Image inspection tools

- **Discovery**
  - Netflix‑style home page with content rails
  - Anonymous aggregate social insights such as reader counts and `Popular with Others`
  - `Continue Reading`, `Jump Back In`, `Trending`, and `New from Following`
  - Library Timeline for character and team histories generated from embedded metadata
  - Recommendations by creator or metadata
  - Random gems, recently updated series

- **Reports Dashboard**
  - Missing Issues
  - Storage Analysis
  - Duplicate Detector
  - Metadata Health
  - Corrupt / Low Page Count

- **Scanning**
  - Background ScanManager with priority queues
  - Timestamp bubbling for updated series
  - Physical page count validation
  - ⚡ Parallel Thumbnail Generation (Optional)
    - Multiprocessing pipeline for thumbnails
    - Distributes image decoding, resizing, WebP encoding, and palette extraction across CPU cores
    - Dedicated writer process batches SQLite commits safely
    - On an 8‑core i7 6th gen., 3,541 comics dropped from 13m40s → 1m58s
    - Fully opt‑in via Settings; 0 = auto (use all cores), values above CPU count are clamped

- **Visuals**
  - Dynamic backgrounds from cover colors
  - Cover Browser gallery mode

- **Format Support**
  - Native archive support for `CBZ` and `CBR`
  - Backend image-pipeline compatibility for `AVIF`
  - Experimental backend support for `JPEG XL` (`JXL`), with browser-dependent native reading support

- **Enrichment**
  - Auto‑populated event descriptions
  - Reading time estimates

- **Search**
  - Advanced rule‑based search builder
  - Saved searches → Smart Lists
  - Secure autocomplete

- **OPDS Support**
  - OPDS 1.2 compliant feeds
  - Dublin Core metadata
  - Legacy client authentication
  - User dashboard integration

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
- Collections, Reading Lists, Stacks, Smart Lists
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


## 📌 Roadmap
- Improve Documentation
- Expanded OPDS support
- Localization
- Enhanced WebP transcoding pipeline (JXL, AVIF to WebP)
- Additional unit test coverage
- Migration tooling improvements
- Pin libraries to front page
- Improve admin Add Library dialog to be able to browse to a folder rather than type it in
- Support multiple folder locations per library
- Light metadata editing with file writeback (Tentative)

## 🤝 Contributing
Parker is early‑stage but stable. Contributions are welcome!
Please check the issues list for open tasks, or propose new features via pull requests.

## 📜 License
MIT License

