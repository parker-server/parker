import json
from pathlib import Path
from typing import Optional

from app.core.text_utils import normalize_title


class EnrichmentService:
    def __init__(self):
        self.local_db = {}
        self._load_local_db()

    def _load_local_db(self):
        """Load the JSON seed file into memory"""
        try:
            path = Path("app/data/events.json")
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    self.local_db = json.load(f)
        except Exception as e:
            print(f"Failed to load event descriptions: {e}")

    def _normalize(self, text: str) -> str:
        return normalize_title(text)

    def get_description(self, event_name: str) -> Optional[str]:
        """
        Try to find a description.
        1. Local JSON (Fast, Curated)
        2. Wikipedia API (Optional, Networked)
        """
        # 1. Try exact match (Normalized)
        # e.g. "Crisis on Infinite Earths" -> "crisis on infinite earths"
        key = self._normalize(event_name)
        if key in self.local_db:
            return self.local_db[key]

        # 2. Try Quote Prefix Fallback (The "ComicVine Fix")
        # Pattern: "Context" Event Name
        # e.g. "Batman" The Resurrection of Ra's al Ghul
        if '"' in event_name:
            # split on double quote, take the last part (the actual event name)
            # "Batman" Event -> ['', 'Batman', ' Event']
            parts = event_name.split('"')

            # We expect at least 3 parts (empty pre-quote, context, post-quote)
            if len(parts) >= 3:
                # The event name is everything after the last quote
                suffix = parts[-1].strip()

                # Normalize that suffix (removes "The", lowercases, etc)
                suffix_key = self._normalize(suffix)

                # Check if the clean name exists in our seed file
                if suffix_key in self.local_db:
                    return self.local_db[suffix_key]

        # TODO: Implement later as this will need to be done in a BG task as the scanner is synchronous and this will block
        # 3. Wikipedia Lookup (Clean API, no scraping)
        # We only try this if the event name looks "significant" (e.g. > 3 chars)
        #if len(key) > 3:
        #    return await self._fetch_wikipedia_summary(event_name)

        return None

    async def _fetch_wikipedia_summary(self, query: str) -> Optional[str]:
        """
        Hit the Wikipedia Summary API.
        Returns the extraction text if high confidence.
        """
        try:
            # Wikipedia API expects Title Case usually, but handles search well
            # endpoint: https://en.wikipedia.org/api/rest_v1/page/summary/{title}

            async with httpx.AsyncClient() as client:
                # We assume the event name is the page title.
                # Spaces should be underscores for the API
                formatted_query = query.replace(" ", "_")

                url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{formatted_query}"
                resp = await client.get(url, timeout=2.0, follow_redirects=True)

                if resp.status_code == 200:
                    data = resp.json()

                    # Basic validation: ensure it's a "standard" page
                    if data.get("type") == "standard":
                        return data.get("extract")

        except Exception:
            # Fail silently on network issues
            return None
        return None