from app.services.enrichment import EnrichmentService


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def get(self, url, params=None):
        self.requests.append({"url": url, "params": params})
        if not self.responses:
            raise AssertionError(f"Unexpected HTTP request: {url}")
        return self.responses.pop(0)


def _service_with_client(client):
    return EnrichmentService(
        allow_online=True,
        client_factory=lambda **_kwargs: client,
    )


def test_lookup_description_uses_local_seed_and_quoted_suffix():
    service = EnrichmentService()

    result = service.lookup_description('"DC" Armageddon 2001')

    assert result.source == "local"
    assert "Monarch" in result.description


def test_lookup_description_uses_direct_wikipedia_summary_for_comics_event():
    client = FakeClient([
        FakeResponse(payload={
            "type": "standard",
            "title": "Lazarus Planet",
            "description": "DC Comics crossover event",
            "extract": (
                "Lazarus Planet is a DC Comics crossover event. "
                "It was published across several titles in 2023. "
                "This third sentence should be trimmed."
            ),
        }),
    ])
    service = _service_with_client(client)
    service.local_db = {}

    result = service.lookup_description("Lazarus Planet")

    assert result.source == "wikipedia"
    assert result.matched_title == "Lazarus Planet"
    assert result.description == (
        "Lazarus Planet is a DC Comics crossover event. "
        "It was published across several titles in 2023."
    )


def test_lookup_description_rejects_non_comics_page_before_disambiguated_summary():
    client = FakeClient([
        FakeResponse(payload={
            "type": "standard",
            "title": "American Civil War",
            "description": "Civil war in the United States",
            "extract": "The American Civil War was a civil war in the United States.",
        }),
        FakeResponse(payload={
            "type": "standard",
            "title": "Civil War II (comics)",
            "description": "Marvel Comics crossover storyline",
            "extract": "Civil War II is a comic book crossover storyline published by Marvel Comics.",
        }),
    ])
    service = _service_with_client(client)
    service.local_db = {}

    result = service.lookup_description("Civil War II")

    assert result.source == "wikipedia"
    assert result.matched_title == "Civil War II (comics)"
    assert result.description == "Civil War II is a comic book crossover storyline published by Marvel Comics."


def test_lookup_description_uses_wikipedia_search_when_direct_titles_miss():
    client = FakeClient([
        FakeResponse(status_code=404),
        FakeResponse(status_code=404),
        FakeResponse(status_code=404),
        FakeResponse(payload={
            "pages": [
                {"key": "X-Men:_Messiah_Complex", "title": "X-Men: Messiah Complex"},
            ],
        }),
        FakeResponse(payload={
            "type": "standard",
            "title": "X-Men: Messiah Complex",
            "description": "Marvel Comics crossover storyline",
            "extract": "Messiah Complex is an X-Men crossover storyline published by Marvel Comics.",
        }),
    ])
    service = _service_with_client(client)
    service.local_db = {}

    result = service.lookup_description("Messiah Complex")

    assert result.source == "wikipedia"
    assert result.matched_title == "X-Men: Messiah Complex"
    assert client.requests[3]["params"] == {"q": '"Messiah Complex" comics', "limit": 5}


def test_lookup_description_honors_online_request_budget():
    client = FakeClient([
        FakeResponse(status_code=404),
        FakeResponse(status_code=404),
        FakeResponse(status_code=404),
    ])
    service = EnrichmentService(
        allow_online=True,
        client_factory=lambda **_kwargs: client,
        max_online_requests=3,
    )
    service.local_db = {}

    result = service.lookup_description("Missing Event")

    assert result.description is None
    assert len(client.requests) == 3
    assert all(request["params"] is None for request in client.requests)
