from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Iterable
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from lxml import html

from app.core.text_utils import normalize_title


COMICBOOKROUNDUP_BASE_URL = "https://comicbookroundup.com"
COMICBOOKROUNDUP_SEARCH_URL = f"{COMICBOOKROUNDUP_BASE_URL}/search-results?keyword="

_DATE_PATTERN = re.compile(
    r"\b("
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")\s+(\d{1,2}),\s+(\d{4})\b",
    re.IGNORECASE,
)
_RATING_PATTERN = re.compile(r"^(?:N/A|\d+(?:\.\d+)?)$")
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class ComicBookRoundupIssueQuery:
    series_title: str
    issue_number: str | None = None
    issue_title: str | None = None
    publisher: str | None = None
    release_date: date | None = None
    writers: tuple[str, ...] = ()
    artists: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComicBookRoundupCandidate:
    url: str
    title: str
    kind: str


@dataclass(frozen=True)
class ComicBookRoundupReview:
    review_type: str
    source: str
    author: str | None
    score: float | None
    review_date: date | None
    excerpt: str | None
    full_review_url: str | None = None


@dataclass(frozen=True)
class ComicBookRoundupIssuePage:
    url: str
    title: str
    publisher: str | None = None
    release_date: date | None = None
    writers: tuple[str, ...] = ()
    artists: tuple[str, ...] = ()
    reviews: tuple[ComicBookRoundupReview, ...] = ()


@dataclass(frozen=True)
class ComicBookRoundupMatch:
    page: ComicBookRoundupIssuePage
    score: float
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComicBookRoundupLookupResult:
    query: ComicBookRoundupIssueQuery
    matches: tuple[ComicBookRoundupMatch, ...]
    searched_keywords: tuple[str, ...]
    candidate_count: int

    @property
    def best_match(self) -> ComicBookRoundupMatch | None:
        return self.matches[0] if self.matches else None

    @property
    def is_confident(self) -> bool:
        if not self.matches:
            return False
        best = self.matches[0]
        runner_up = self.matches[1] if len(self.matches) > 1 else None
        return best.score >= 75 and (runner_up is None or best.score - runner_up.score >= 12)


class ComicBookRoundupClient:
    def __init__(
        self,
        *,
        timeout: float = 10.0,
        user_agent: str = "Parker ComicBookRoundup POC/0.1",
    ):
        self.timeout = timeout
        self.user_agent = user_agent

    async def fetch_text(self, url: str) -> str:
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": self.user_agent},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    async def search_candidates(self, keyword: str) -> list[ComicBookRoundupCandidate]:
        html_text = await self.fetch_text(f"{COMICBOOKROUNDUP_SEARCH_URL}{quote_plus(keyword)}")
        return parse_search_results(html_text)

    async def lookup_issue(
        self,
        query: ComicBookRoundupIssueQuery,
        *,
        max_candidates: int = 8,
    ) -> ComicBookRoundupLookupResult:
        searched_keywords = build_search_keywords(query)
        candidates: list[ComicBookRoundupCandidate] = []
        seen_candidate_urls: set[str] = set()

        for keyword in searched_keywords:
            for candidate in (await self.search_candidates(keyword))[:max_candidates]:
                if candidate.url in seen_candidate_urls:
                    continue
                seen_candidate_urls.add(candidate.url)
                candidates.append(candidate)

        candidates.sort(key=lambda candidate: _score_candidate_hint(query, candidate), reverse=True)
        candidates_to_expand = candidates[:max_candidates]

        issue_urls: list[str] = []
        seen_issue_urls: set[str] = set()
        for candidate in candidates_to_expand:
            if candidate.kind == "issue":
                if candidate.url not in seen_issue_urls:
                    seen_issue_urls.add(candidate.url)
                    issue_urls.append(candidate.url)
                continue

            if candidate.kind != "series":
                continue

            series_html = await self.fetch_text(candidate.url)
            for issue_url in parse_series_issue_links(series_html, candidate.url):
                if issue_url not in seen_issue_urls:
                    seen_issue_urls.add(issue_url)
                    issue_urls.append(issue_url)

        issue_urls.sort(key=lambda issue_url: _score_issue_url_hint(query, issue_url), reverse=True)
        matches: list[ComicBookRoundupMatch] = []
        for issue_url in issue_urls[:max_candidates]:
            issue_html = await self.fetch_text(issue_url)
            page = parse_issue_page(issue_html, issue_url)
            score, reasons = score_issue_match(query, page)
            matches.append(ComicBookRoundupMatch(page=page, score=score, reasons=tuple(reasons)))

        matches.sort(key=lambda match: match.score, reverse=True)
        return ComicBookRoundupLookupResult(
            query=query,
            matches=tuple(matches),
            searched_keywords=tuple(searched_keywords),
            candidate_count=len(candidates),
        )


def build_search_keywords(query: ComicBookRoundupIssueQuery) -> list[str]:
    keywords: list[str] = []
    parts = [query.series_title]
    if query.issue_title:
        parts.append(query.issue_title)
    if query.issue_number:
        parts.append(str(query.issue_number))
    keywords.append(" ".join(part for part in parts if part))

    if query.issue_title:
        keywords.append(f"{query.series_title} {query.issue_title}")
    if query.issue_number:
        keywords.append(f"{query.series_title} {query.issue_number}")
    if query.publisher:
        keywords.append(f"{query.series_title} {query.publisher}")
    keywords.append(query.series_title)

    deduped: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = re.sub(r"\s+", " ", keyword).strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            deduped.append(cleaned)
    return deduped


def parse_search_results(html_text: str, base_url: str = COMICBOOKROUNDUP_BASE_URL) -> list[ComicBookRoundupCandidate]:
    doc = html.fromstring(html_text)
    candidates: list[ComicBookRoundupCandidate] = []
    seen_urls: set[str] = set()

    for link in doc.xpath("//a[contains(@href, '/comic-books/reviews/')]"):
        href = link.get("href")
        if not href:
            continue

        url = _absolute_url(href, base_url)
        if url in seen_urls:
            continue

        kind = _classify_review_url(url)
        if kind not in {"series", "issue"}:
            continue

        title = _clean_space(link.text_content())
        if not title:
            title = _title_from_url(url)

        seen_urls.add(url)
        candidates.append(ComicBookRoundupCandidate(url=url, title=title, kind=kind))

    return candidates


def parse_series_issue_links(html_text: str, series_url: str) -> list[str]:
    doc = html.fromstring(html_text)
    normalized_series_url = series_url.rstrip("/")
    issue_urls: list[str] = []
    seen: set[str] = set()

    for link in doc.xpath("//a[contains(@href, '/comic-books/reviews/')]"):
        href = link.get("href")
        if not href:
            continue

        url = _absolute_url(href, normalized_series_url)
        if not url.startswith(normalized_series_url + "/"):
            continue
        if _classify_review_url(url) != "issue":
            continue
        if url in seen:
            continue

        seen.add(url)
        issue_urls.append(url)

    return issue_urls


def parse_issue_page(html_text: str, url: str) -> ComicBookRoundupIssuePage:
    doc = html.fromstring(html_text)
    lines = _document_lines(doc)
    title = _first_text(doc, "//h1") or _title_from_url(url)
    publisher = _extract_publisher(doc)
    release_date = _first_date(lines)
    writers = _extract_named_row(doc, lines, "Writer")
    artists = _extract_named_row(doc, lines, "Artist")
    review_links = [
        _absolute_url(link.get("href"), url)
        for link in doc.xpath("//a[contains(normalize-space(.), 'Read Full Review')]")
        if link.get("href")
    ]
    reviews = _extract_reviews(lines, review_links)

    return ComicBookRoundupIssuePage(
        url=url,
        title=title,
        publisher=publisher,
        release_date=release_date,
        writers=tuple(writers),
        artists=tuple(artists),
        reviews=tuple(reviews),
    )


def score_issue_match(
    query: ComicBookRoundupIssueQuery,
    page: ComicBookRoundupIssuePage,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if query.publisher and page.publisher:
        if _same_publisher(query.publisher, page.publisher):
            score += 18
            reasons.append("publisher")
        else:
            score -= 20
            reasons.append("publisher-mismatch")

    title_score = _title_match_score(query, page.title)
    score += title_score
    if title_score >= 35:
        reasons.append("strong-title")
    elif title_score >= 22:
        reasons.append("partial-title")

    if query.issue_title:
        issue_title_key = _compact_title(query.issue_title)
        page_key = _compact_title(page.title)
        if issue_title_key and issue_title_key in page_key:
            score += 16
            reasons.append("issue-title")
        elif issue_title_key:
            score -= 10
            reasons.append("issue-title-mismatch")

    if query.issue_number:
        expected_number = _normalize_issue_number(query.issue_number)
        page_number = _extract_issue_number(page.title)
        if expected_number and page_number == expected_number:
            score += 18
            reasons.append("issue-number")
        elif expected_number:
            score -= 12
            reasons.append("issue-number-mismatch")

    if query.release_date and page.release_date:
        if query.release_date == page.release_date:
            score += 18
            reasons.append("release-date")
        elif query.release_date.year == page.release_date.year and query.release_date.month == page.release_date.month:
            score += 8
            reasons.append("release-month")

    creator_overlap = _creator_overlap(query.writers, page.writers) + _creator_overlap(query.artists, page.artists)
    if creator_overlap:
        score += min(creator_overlap * 4, 12)
        reasons.append("creators")

    if page.reviews:
        score += 4
        reasons.append("has-reviews")

    return max(score, 0.0), reasons


def _extract_reviews(lines: list[str], full_review_links: list[str]) -> list[ComicBookRoundupReview]:
    reviews: list[ComicBookRoundupReview] = []
    critic_start = _section_header_index(lines, "CRITIC REVIEWS")
    user_start = _section_header_index(lines, "USER REVIEWS")
    footer_start = _first_footer_index(lines)
    if critic_start is not None:
        critic_end = user_start if user_start is not None else (footer_start or len(lines))
        reviews.extend(_extract_review_section(lines[critic_start + 1:critic_end], "critic", full_review_links))
    if user_start is not None:
        user_end = footer_start or len(lines)
        reviews.extend(_extract_review_section(lines[user_start + 1:user_end], "user", []))
    return reviews


def _extract_review_section(
    section_lines: list[str],
    review_type: str,
    full_review_links: list[str],
) -> list[ComicBookRoundupReview]:
    reviews: list[ComicBookRoundupReview] = []
    link_index = 0
    index = 0
    while index < len(section_lines):
        line = section_lines[index]
        if not _RATING_PATTERN.match(line):
            index += 1
            continue

        score = _parse_score(line)
        source_line_index = _next_content_line(section_lines, index + 1)
        if source_line_index is None:
            break
        source_line = section_lines[source_line_index]
        if source_line.lower() == "image":
            source_line_index = _next_content_line(section_lines, source_line_index + 1)
            if source_line_index is None:
                break
            source_line = section_lines[source_line_index]

        source, author = _split_source_author(source_line, review_type)
        date_index = _next_date_line(section_lines, source_line_index + 1)
        review_date = _parse_date(section_lines[date_index]) if date_index is not None else None

        body_start = (date_index + 1) if date_index is not None else source_line_index + 1
        body_lines: list[str] = []
        cursor = body_start
        while cursor < len(section_lines):
            next_line = section_lines[cursor]
            if _RATING_PATTERN.match(next_line):
                break
            if next_line in {"Back to Top", "+ Like", "Comment", "Rate / Write A Review", "Submit Request"}:
                cursor += 1
                continue
            if next_line.lower() == "read full review":
                cursor += 1
                continue
            if next_line.startswith("+ Like") or next_line == "Reviews for the Week of...":
                break
            body_lines.append(next_line.replace("Read Full Review", "").strip())
            cursor += 1

        full_review_url = None
        if review_type == "critic" and link_index < len(full_review_links):
            full_review_url = full_review_links[link_index]
            link_index += 1

        reviews.append(
            ComicBookRoundupReview(
                review_type=review_type,
                source=source,
                author=author,
                score=score,
                review_date=review_date,
                excerpt=_clean_space(" ".join(line for line in body_lines if line)) or None,
                full_review_url=full_review_url,
            )
        )
        index = max(cursor, index + 1)

    return reviews


def _classify_review_url(url: str) -> str | None:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    try:
        reviews_index = parts.index("reviews")
    except ValueError:
        return None

    remainder = parts[reviews_index + 1:]
    if len(remainder) == 2:
        return "series"
    if len(remainder) >= 3:
        return "issue"
    return None


def _score_candidate_hint(
    query: ComicBookRoundupIssueQuery,
    candidate: ComicBookRoundupCandidate,
) -> float:
    score = _title_match_score(query, candidate.title)
    if query.publisher:
        parsed = urlparse(candidate.url)
        parts = [part for part in parsed.path.split("/") if part]
        try:
            publisher_slug = parts[parts.index("reviews") + 1]
        except (ValueError, IndexError):
            publisher_slug = ""
        if _same_publisher(publisher_slug.replace("-", " "), query.publisher):
            score += 10
    if candidate.kind == "issue":
        score += 4
    return score


def _score_issue_url_hint(query: ComicBookRoundupIssueQuery, issue_url: str) -> float:
    score = 0.0
    issue_number = _normalize_issue_number(query.issue_number)
    if issue_number and _url_ends_with_issue_number(issue_url, issue_number):
        score += 25

    if query.issue_title:
        title_slug = normalize_title(query.issue_title.replace("-", " "))
        url_slug = normalize_title(urlparse(issue_url).path.split("/")[-1].replace("-", " "))
        if title_slug and title_slug in url_slug:
            score += 12
    return score


def _url_ends_with_issue_number(issue_url: str, issue_number: str) -> bool:
    slug = urlparse(issue_url).path.rstrip("/").split("/")[-1]
    slug_number = issue_number.replace(".", "-")
    return bool(re.search(rf"(?:^|-){re.escape(slug_number)}$", slug))


def _first_footer_index(lines: list[str]) -> int | None:
    footer_markers = ("Reviews for the Week of...", "2025 In Review:", "Home")
    for index, line in enumerate(lines):
        if any(line.startswith(marker) for marker in footer_markers):
            return index
    return None


def _absolute_url(href: str | None, base_url: str) -> str:
    if not href:
        return base_url
    return urljoin(base_url, href).split("#", 1)[0]


def _document_lines(doc) -> list[str]:
    return [_clean_space(line) for line in doc.text_content().splitlines() if _clean_space(line)]


def _first_text(doc, xpath: str) -> str | None:
    values = doc.xpath(xpath)
    if not values:
        return None
    return _clean_space(values[0].text_content())


def _clean_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _extract_publisher(doc) -> str | None:
    for link in doc.xpath("//a[contains(@href, '/comic-books/reviews/')]"):
        href = link.get("href") or ""
        parts = [part for part in urlparse(_absolute_url(href, COMICBOOKROUNDUP_BASE_URL)).path.split("/") if part]
        if len(parts) != 3 or parts[-2] != "reviews":
            continue
        text = _clean_space(link.text_content())
        if text and "comics" in normalize_title(text):
            return text
    return None


def _first_date(lines: list[str]) -> date | None:
    for line in lines[:120]:
        parsed = _parse_date(line)
        if parsed:
            return parsed
    return None


def _parse_date(value: str) -> date | None:
    match = _DATE_PATTERN.search(value)
    if not match:
        return None
    month_name, day, year = match.groups()
    month = _MONTHS[month_name.lower()]
    return date(int(year), month, int(day))


def _extract_named_row(doc, lines: list[str], label: str) -> list[str]:
    names: list[str] = []
    for node in doc.xpath(f"//*[self::dt or self::th or self::td][normalize-space() = '{label}']"):
        for sibling in node.itersiblings():
            if sibling.tag in {"dd", "td"}:
                names.extend(_split_names(sibling.text_content()))
                break

    for index, line in enumerate(lines):
        if line == label and index + 1 < len(lines):
            names.extend(_split_names(lines[index + 1]))
        elif line.startswith(f"{label} |"):
            names.extend(_split_names(line.split("|", 1)[1]))
        elif line.startswith(label) and len(line) > len(label):
            names.extend(_split_names(line[len(label):]))
    return _dedupe_names(names)


def _split_names(value: str) -> list[str]:
    return [_clean_space(name) for name in re.split(r",| and ", value) if _clean_space(name)]


def _dedupe_names(names: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = normalize_title(name)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _line_index(lines: list[str], needle: str) -> int | None:
    needle_key = needle.lower()
    for index, line in enumerate(lines):
        if line.lower() == needle_key or line.upper().startswith(needle):
            return index
    return None


def _section_header_index(lines: list[str], needle: str) -> int | None:
    needle_key = needle.lower()
    for index, line in enumerate(lines):
        line_key = line.lower()
        if line_key == needle_key:
            return index
        if line_key.startswith(needle_key) and "(" not in line:
            return index
    return None


def _next_content_line(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index] and lines[index] not in {"Back to Top"}:
            return index
    return None


def _next_date_line(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if _parse_date(lines[index]):
            return index
    return None


def _parse_score(value: str) -> float | None:
    if value == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _split_source_author(value: str, review_type: str) -> tuple[str, str | None]:
    if review_type == "critic" and " - " in value:
        source, author = value.split(" - ", 1)
        return _clean_space(source), _clean_space(author)
    return _clean_space(value), None


def _same_normalized(left: str, right: str) -> bool:
    return normalize_title(left) == normalize_title(right)


def _same_publisher(left: str, right: str) -> bool:
    return _publisher_key(left) == _publisher_key(right)


def _publisher_key(value: str) -> str:
    key = normalize_title(value)
    for suffix in ("comics", "comic", "publishing", "publisher", "entertainment"):
        if key.endswith(f" {suffix}"):
            key = key[: -(len(suffix) + 1)].strip()
    return key


def _title_match_score(query: ComicBookRoundupIssueQuery, page_title: str) -> float:
    page_key = _compact_title(page_title)
    expected_titles = _expected_titles(query)
    if not expected_titles:
        return 0.0

    best = 0.0
    for title in expected_titles:
        title_key = _compact_title(title)
        if not title_key:
            continue
        if title_key == page_key:
            best = max(best, 48.0)
        elif title_key in page_key or page_key in title_key:
            best = max(best, 28.0)
        else:
            best = max(best, SequenceMatcher(None, title_key, page_key).ratio() * 35)
    return best


def _expected_titles(query: ComicBookRoundupIssueQuery) -> list[str]:
    titles = [query.series_title]
    if query.issue_number:
        titles.append(f"{query.series_title} #{query.issue_number}")
    if query.issue_title:
        titles.append(f"{query.series_title}: {query.issue_title}")
        if query.issue_number:
            titles.append(f"{query.series_title}: {query.issue_title} #{query.issue_number}")
    return titles


def _compact_title(value: str) -> str:
    normalized = normalize_title(value)
    return re.sub(r"\b(19|20)\d{2}\b", "", normalized).strip()


def _normalize_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lstrip("#")
    match = re.match(r"(\d+(?:\.\d+)?)", value)
    return match.group(1) if match else normalize_title(value)


def _extract_issue_number(title: str) -> str | None:
    match = re.search(r"#\s*(\d+(?:\.\d+)?)\b", title)
    if match:
        return match.group(1)
    trailing = re.search(r"\b(\d+(?:\.\d+)?)\s*$", title)
    return trailing.group(1) if trailing else None


def _creator_overlap(expected: Iterable[str], actual: Iterable[str]) -> int:
    actual_keys = {normalize_title(name) for name in actual}
    return sum(1 for name in expected if normalize_title(name) in actual_keys)


def _title_from_url(url: str) -> str:
    last_segment = [part for part in urlparse(url).path.split("/") if part][-1]
    return last_segment.replace("-", " ").replace("%28", "(").replace("%29", ")").title()
