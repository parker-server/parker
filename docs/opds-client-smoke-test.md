# OPDS Client Smoke Test

Status: Draft

This checklist is for manual OPDS validation against real reader apps. It is
intended to answer a narrow question: when a client behaves differently from
another client, is Parker emitting something wrong, or is the reader handling a
valid OPDS catalog in its own way?

Keep this smoke test small and boring. A tiny catalog makes client behavior much
easier to compare than a full personal library.

## Goals

- Confirm that a fresh OPDS client can authenticate with a Parker OPDS key.
- Confirm that navigation feeds, thumbnails, and acquisition links work.
- Compare Moon+ Reader and Librera behavior against the same fixture.
- Capture enough HTTP evidence before adding any client-specific workaround.

## Non-Goals

- Do not use this as a replacement for automated OPDS API tests.
- Do not commit copyrighted sample comics to the repository.
- Do not treat fake CBR files as meaningful client compatibility evidence.
- Do not add Librera-specific behavior unless request logs show a clear, standards-friendly fix.

## Fixture Shape

Create a temporary library with this minimum shape:

- Library: `OPDS Smoke Library`
- Series: `OPDS Smoke Series`
- Volume: `1`
- Issue 1: a tiny known-good CBZ
- Issue 2: another tiny known-good CBZ
- Optional Issue 3: a known-good RAR4 CBR, if available
- Collection: `OPDS Smoke Collection`, containing Issue 1 and Issue 2
- Reading List: `OPDS Smoke Reading Order`, containing Issue 2 first, then Issue 1

The CBZ files should be real ZIP archives with normal image files inside. A
minimal test archive with one or two small PNG/JPEG pages is enough. The optional
CBR should be a real RAR archive from a source you are allowed to use; do not
rename a ZIP file to `.cbr`.

## Parker Setup

1. Run all pending Alembic migrations.
2. Enable OPDS in admin settings.
3. Create or reuse a non-admin test user.
4. Give the test user access to `OPDS Smoke Library`.
5. Create a revocable OPDS key for the test user.
6. Scan the smoke library and confirm the web UI shows the series, collection, and reading list.

Use this catalog URL in clients:

```text
http://<server-host>:<port>/opds/
```

Use the Parker username as the OPDS username and the OPDS key as the password.

## Baseline HTTP Checks

Before testing reader apps, verify the server path with a simple HTTP client or
browser session that can send Basic Auth:

- `GET /opds/` returns `200` and `application/atom+xml`.
- `GET /opds/libraries/{library_id}` returns the smoke series.
- `GET /opds/series/{series_id}` returns acquisition entries for both CBZ issues.
- `GET /opds/collections` returns `OPDS Smoke Collection`.
- `GET /opds/collections/{collection_id}` returns the collection issues.
- `GET /opds/reading-lists` returns `OPDS Smoke Reading Order`.
- `GET /opds/reading-lists/{list_id}` returns Issue 2 before Issue 1.
- `GET /opds/images/{comic_id}/thumbnail.jpg` returns `image/jpeg`.
- `GET /opds/download/{comic_id}/...` returns the original archive media type.

Expected download media types:

- CBZ: `application/vnd.comicbook+zip`
- CBR: `application/vnd.comicbook-rar`
- PDF: `application/pdf`

## Moon+ Reader Checklist

1. Add the Parker OPDS catalog from a fresh Moon+ Reader profile.
2. Authenticate with the test username and OPDS key.
3. Browse root entries.
4. Browse `OPDS Smoke Library -> OPDS Smoke Series`.
5. Confirm cover thumbnails render.
6. Download Issue 1 CBZ.
7. Open and read Issue 1 CBZ.
8. Browse `Collections -> OPDS Smoke Collection` and download an issue.
9. Browse `Reading Lists -> OPDS Smoke Reading Order` and confirm the order.
10. If testing CBR, download and open the known-good RAR4 CBR.

Expected result: Moon+ Reader can browse, download, and open CBZ issues. CBR
support depends on the archive's RAR version and Moon+ Reader's local support.

## Librera Checklist

1. Add the same Parker OPDS catalog from a fresh Librera profile.
2. Authenticate with the test username and OPDS key.
3. Browse root entries.
4. Browse `OPDS Smoke Library -> OPDS Smoke Series`.
5. Confirm whether normal cover thumbnails render.
6. Tap an issue entry and note whether Librera requests the acquisition URL.
7. Try the same acquisition path from `Collections`.
8. Try the same acquisition path from `Reading Lists`.
9. If acquisition fails, capture Parker access logs before retrying with another client.

Expected result: Librera may browse feeds and display thumbnails while failing to
acquire/open issue entries. Treat that as a client-specific finding unless logs
show Parker returning a bad status, bad media type, or missing auth challenge.

## Log Checklist

For each client, capture whether the reader requests:

- `/opds/`
- `/opds/libraries/{library_id}`
- `/opds/series/{series_id}`
- `/opds/collections`
- `/opds/collections/{collection_id}`
- `/opds/reading-lists`
- `/opds/reading-lists/{list_id}`
- `/opds/images/{comic_id}/thumbnail.jpg`
- `/opds/download/{comic_id}/{filename}`

For each request, record:

- HTTP method
- status code
- authenticated username, if logged
- response content type
- whether the client sent Basic Auth on download and thumbnail requests
- whether the client requested the acquisition URL at all

## Result Matrix

Use this table when comparing clients:

| Client | Add catalog | Browse root | Browse series | Covers | Download CBZ | Open CBZ | Download CBR | Open CBR | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Moon+ Reader |  |  |  |  |  |  |  |  |  |
| Librera |  |  |  |  |  |  |  |  |  |

## Interpreting Failures

Likely Parker issue:

- Feed returns malformed XML.
- Feed links are relative instead of absolute.
- Thumbnail endpoint returns a non-JPEG response.
- Acquisition link returns `401`, `403`, `404`, or the wrong media type for an allowed user.
- Download response omits the archive filename or serves the wrong file.

Likely client issue:

- Another client succeeds against the same URLs.
- The reader never requests the acquisition URL after tapping an issue.
- The reader requests covers but never downloads books.
- The reader drops Basic Auth only on acquisition requests.
- CBZ works but a RAR5 CBR fails locally after download.

Ambiguous:

- The client caches stale feeds.
- The client rewrites or strips filename-bearing acquisition URLs.
- The client behaves differently between Wi-Fi, VPN, and local network paths.

When behavior is ambiguous, preserve the smoke fixture and capture request logs
before changing Parker. A small standards-friendly adjustment is better than a
client-specific workaround that makes the catalog less predictable for everyone
else.
