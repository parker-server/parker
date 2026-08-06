import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import httpx

from app.core.text_utils import normalize_title


@dataclass(frozen=True)
class EnrichmentResult:
    description: Optional[str]
    source: Optional[str] = None
    matched_title: Optional[str] = None


class EnrichmentService:
    WIKIPEDIA_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/page"
    WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"
    REQUEST_HEADERS = {
        "Accept": "application/json",
        "User-Agent": "Parker-Comic-Server event enrichment",
    }

    DIRECT_TITLE_SUFFIXES = (
        "",
        " (comics)",
        " (comic book)",
    )

    COMIC_MARKERS = (
        "comic",
        "comics",
        "comic book",
        "dc comics",
        "marvel comics",
        "image comics",
        "dark horse comics",
        "idw publishing",
        "valiant comics",
    )
    EVENT_MARKERS = (
        "storyline",
        "story arc",
        "crossover",
        "limited series",
        "event",
        "one-shot",
    )
    STRONG_COMIC_EVENT_MARKERS = (
        "comic book crossover",
        "comic book limited series",
        "comic book storyline",
        "comics crossover",
        "crossover storyline",
        "published by dc comics",
        "published by marvel comics",
    )

    def __init__(
        self,
        *,
        allow_online: bool = False,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        timeout: Optional[httpx.Timeout] = None,
        max_online_requests: int = 6,
    ):
        self.allow_online = allow_online
        self.client_factory = client_factory
        self.timeout = timeout or httpx.Timeout(connect=1.0, read=2.0, write=1.0, pool=1.0)
        self.max_online_requests = max(0, max_online_requests)
        self.logger = logging.getLogger(__name__)
        self.local_db = {}
        self._online_cache: dict[str, EnrichmentResult] = {}
        self._load_local_db()

    def _load_local_db(self):
        """Load the JSON seed file into memory"""
        try:
            path = Path(__file__).resolve().parents[1] / "data" / "events.json"
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.local_db = {
                    self._normalize(key): value
                    for key, value in raw.items()
                    if isinstance(key, str) and isinstance(value, str)
                }
        except Exception as e:
            self.logger.warning(f"Failed to load event descriptions: {e}")

    def _normalize(self, text: str) -> str:
        return normalize_title(text)

    def lookup_description(
        self,
        event_name: str,
        *,
        allow_online: Optional[bool] = None,
    ) -> EnrichmentResult:
        """
        Try to find a description.
        1. Local JSON (Fast, Curated)
        2. Wikipedia API (Optional, Networked, bounded)
        """
        variants = self._name_variants(event_name)
        for variant in variants:
            key = self._normalize(variant)
            if key in self.local_db:
                return EnrichmentResult(
                    description=self.local_db[key],
                    source="local",
                    matched_title=variant,
                )

        online_enabled = self.allow_online if allow_online is None else allow_online
        if not online_enabled:
            return EnrichmentResult(description=None)

        cache_key = "|".join(self._normalize(variant) for variant in variants)
        if cache_key in self._online_cache:
            return self._online_cache[cache_key]

        result = self._fetch_wikipedia_description(variants)
        self._online_cache[cache_key] = result
        return result

    def get_description(
        self,
        event_name: str,
        *,
        allow_online: Optional[bool] = None,
    ) -> Optional[str]:
        return self.lookup_description(event_name, allow_online=allow_online).description

    def _name_variants(self, event_name: str) -> list[str]:
        variants = []
        seen = set()

        def add(value: str):
            cleaned = re.sub(r"\s+", " ", value or "").strip()
            key = self._normalize(cleaned)
            if key and key not in seen:
                variants.append(cleaned)
                seen.add(key)

        add(event_name)

        if '"' in event_name:
            suffix = event_name.rsplit('"', 1)[-1]
            add(suffix)

        return variants

    def _fetch_wikipedia_description(self, variants: list[str]) -> EnrichmentResult:
        if not variants or self.max_online_requests <= 0:
            return EnrichmentResult(description=None)

        try:
            with self.client_factory(
                timeout=self.timeout,
                headers=self.REQUEST_HEADERS,
                follow_redirects=True,
            ) as client:
                request_count = 0

                for title in self._direct_titles(variants):
                    if request_count >= self.max_online_requests:
                        break
                    request_count += 1
                    result = self._summary_lookup(client, title, variants)
                    if result.description:
                        return result

                for query in self._search_queries(variants):
                    if request_count >= self.max_online_requests:
                        break
                    request_count += 1
                    for title in self._search_titles(client, query):
                        if request_count >= self.max_online_requests:
                            break
                        request_count += 1
                        result = self._summary_lookup(client, title, variants)
                        if result.description:
                            return result
        except httpx.HTTPError as exc:
            self.logger.debug(f"Wikipedia enrichment lookup failed: {exc}")
        except Exception as exc:
            self.logger.debug(f"Unexpected enrichment lookup failure: {exc}")

        return EnrichmentResult(description=None)

    def _direct_titles(self, variants: list[str]) -> list[str]:
        titles = []
        seen = set()

        for variant in variants[:2]:
            for suffix in self.DIRECT_TITLE_SUFFIXES:
                title = f"{variant}{suffix}"
                key = title.casefold()
                if key not in seen:
                    titles.append(title)
                    seen.add(key)

        return titles

    def _search_queries(self, variants: list[str]) -> list[str]:
        queries = []
        seen = set()
        for variant in variants[:2]:
            for query in (
                f'"{variant}" comics',
                f"{variant} comic book storyline",
            ):
                key = query.casefold()
                if key not in seen:
                    queries.append(query)
                    seen.add(key)
        return queries

    def _summary_lookup(
        self,
        client: httpx.Client,
        title: str,
        variants: list[str],
    ) -> EnrichmentResult:
        encoded_title = quote(title.replace(" ", "_"), safe="")
        response = client.get(f"{self.WIKIPEDIA_SUMMARY_URL}/{encoded_title}")
        if response.status_code != 200:
            return EnrichmentResult(description=None)

        try:
            data = response.json()
        except ValueError:
            return EnrichmentResult(description=None)

        return self._summary_to_result(data, variants)

    def _search_titles(self, client: httpx.Client, query: str) -> list[str]:
        response = client.get(
            self.WIKIPEDIA_SEARCH_URL,
            params={"q": query, "limit": 5},
        )
        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except ValueError:
            return []

        titles = []
        for page in data.get("pages", []):
            title = page.get("key") or page.get("title")
            if not title:
                continue

            titles.append(title)
            if len(titles) >= 3:
                break

        return titles

    def _summary_to_result(self, data: dict, variants: list[str]) -> EnrichmentResult:
        if data.get("type") != "standard":
            return EnrichmentResult(description=None)

        extract = self._clean_text(data.get("extract") or "")
        title = self._clean_text(data.get("title") or "")
        description = self._clean_text(data.get("description") or "")

        if not extract or not title:
            return EnrichmentResult(description=None)

        searchable_text = f"{title} {description} {extract}"
        if not self._title_matches_event(title, variants):
            return EnrichmentResult(description=None)
        if not self._has_comic_event_context(searchable_text):
            return EnrichmentResult(description=None)

        return EnrichmentResult(
            description=self._trim_extract(extract),
            source="wikipedia",
            matched_title=title,
        )

    def _title_matches_event(self, title: str, variants: list[str]) -> bool:
        title_key = self._normalize(title)
        base_title_key = self._normalize(re.sub(r"\s*\([^)]*\)", "", title))

        for variant in variants:
            variant_key = self._normalize(variant)
            if len(variant_key) < 4:
                continue

            if title_key == variant_key or base_title_key == variant_key:
                return True
            if title_key.startswith(f"{variant_key} ") or title_key.endswith(f" {variant_key}"):
                return True
            if f" {variant_key} " in f" {title_key} " and self._title_has_comic_disambiguator(title):
                return True

        return False

    def _title_has_comic_disambiguator(self, title: str) -> bool:
        title_key = self._normalize(title)
        return any(marker in title_key for marker in ("comics", "comic book", "dc comics", "marvel comics"))

    def _has_comic_event_context(self, text: str) -> bool:
        normalized = self._clean_text(text).casefold()

        if any(marker in normalized for marker in self.STRONG_COMIC_EVENT_MARKERS):
            return True

        has_comic_marker = any(marker in normalized for marker in self.COMIC_MARKERS)
        has_event_marker = any(marker in normalized for marker in self.EVENT_MARKERS)
        if not has_comic_marker or not has_event_marker:
            return False

        if " film" in normalized and not any(
            marker in normalized
            for marker in ("comic book", "storyline", "story arc", "limited series")
        ):
            return False

        return True

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", "", text or "")
        text = html.unescape(text)
        return re.sub(r"\s+", " ", text).strip()

    def _trim_extract(self, extract: str) -> str:
        text = self._clean_text(extract)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        description = " ".join(sentence for sentence in sentences[:2] if sentence).strip()

        if len(description) > 700:
            description = description[:697].rsplit(" ", 1)[0].rstrip() + "..."

        return description
