# Parker Comic Server

Parker is a self‑hosted media server for comic books (CBZ/CBR). It follows a **“Filesystem is Truth”** philosophy, parsing metadata directly from `ComicInfo.xml` inside archives. Parker is currently at **Version 0.1.17 (Stable)**.

https://github.com/parker-server/parker/wiki/Getting-Started

---

## ✨ Features

- **Library Management**
  - Hierarchy: `Library → Series → Volume → Comic`
  - Rich metadata (credits, tags, page counts, colors)
  - Reading Lists, Collections, Story Arcs, Pull Lists, Smart Lists

- **User System**
  - Multi‑library access with row‑level security
  - Avatar uploads
  - Hybrid authentication (JWT + secure cookies)

- **Reader**
  - Context‑aware navigation (series, volume, lists)
  - Manga mode (RTL), double‑page spreads
  - Zero‑latency engine with preloading and swipe navigation
  - Image inspection tools

- **Discovery**
  - Netflix‑style home page with content rails
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
    - On an 8‑core i7, 3,541 comics dropped from 13m40s → 1m58s
    - Fully opt‑in via Settings; 0 = auto (use all cores), values above CPU count are clamped

- **Visuals**
  - Dynamic backgrounds from cover colors
  - Cover Browser gallery mode

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
- Collections, Reading Lists, Pull Lists, Smart Lists
- User access control
- Batch operations (mark read/unread, add to lists)

---

## 🚀 Getting Started

https://github.com/parker-server/parker/wiki/Getting-Started


## 📌 Roadmap
- Documentation
- Remaining UI polish (remove the remaining native JS dialogs with custom modals)
- Expanded OPDS support
- Localization
- Enhanced WebP transcoding pipeline
- Additional unit test coverage
- Migration tooling improvements
- Add support to leverage the Age restriction attribute against a user
- Pin libraries to front page
- Improve admin Add Library dialog to be able to browse to a folder rather than type it in
- Support multiple folder locations per library
- Light metadata editing with file writeback

## 🤝 Contributing
Parker is early‑stage but stable. Contributions are welcome!
Please check the issues list for open tasks, or propose new features via pull requests.

## 📜 License
MIT License
