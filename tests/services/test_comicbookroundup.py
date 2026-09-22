from datetime import date

from app.services.comicbookroundup import (
    ComicBookRoundupIssuePage,
    ComicBookRoundupIssueQuery,
    parse_issue_page,
    parse_search_results,
    parse_series_issue_links,
    score_issue_match,
)


SEARCH_HTML = """
<html>
  <body>
    <a href="/comic-books/reviews/marvel-comics">Marvel Comics</a>
    <a href="/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29">
      Challenges Of Doom (2026)
    </a>
    <a href="/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1">
      Challenges Of Doom: Spider-Man #1
    </a>
    <a href="/comic-books/reviewer/josh-allen">Josh Allen</a>
  </body>
</html>
"""


SERIES_HTML = """
<html>
  <body>
    <h1>Challenges Of Doom (2026)</h1>
    <table>
      <tr>
        <td><a href="/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/mr-fantastic-1">Mr. Fantastic 1</a></td>
      </tr>
      <tr>
        <td><a href="/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1">Spider-Man 1</a></td>
      </tr>
    </table>
  </body>
</html>
"""


ISSUE_HTML = """
<html>
  <body>
    <nav>
      <a href="/comic-books/reviews/marvel-comics">Marvel Comics</a>
      <a href="/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29">Series</a>
    </nav>
    <time>September 09, 2026</time>
    <h1>Challenges Of Doom: Spider-Man #1</h1>
    <dl>
      <dt>Writer</dt><dd>Al Ewing</dd>
      <dt>Artist</dt><dd>David Messina</dd>
    </dl>
    <h2>CRITIC REVIEWS</h2>
    <ul>
      <li>
        <strong>10</strong>
        <h3>Nerd Initiative - Megan Nichole</h3>
        <span>Sep 09, 2026</span>
        <p>No matter if you're a Spider-Man fan or Doctor Doom fan, you're going to love this issue.</p>
        <a href="https://nerdinitiative.com/review">Read Full Review</a>
      </li>
      <li>
        <strong>9.0</strong>
        <h3>AIPT - Collier Jennings</h3>
        <span>Sep 09, 2026</span>
        <p>Challenges of Doom: Spider-Man #1 will make readers see Spider-Man and Doctor Doom in a new light.</p>
        <a href="https://aiptcomics.com/review">Read Full Review</a>
      </li>
    </ul>
    <h2>USER REVIEWS</h2>
    <ul>
      <li>
        <strong>9.0</strong>
        <h3>daspidaboy</h3>
        <span>Sep 14, 2026</span>
        <p>This is the best spider-man issue of 2026.</p>
        <a>+ Like</a>
      </li>
    </ul>
  </body>
</html>
"""


def test_parse_search_results_collects_review_candidates_only():
    candidates = parse_search_results(SEARCH_HTML)

    assert [(candidate.kind, candidate.title) for candidate in candidates] == [
        ("series", "Challenges Of Doom (2026)"),
        ("issue", "Challenges Of Doom: Spider-Man #1"),
    ]


def test_parse_series_issue_links_uses_actual_issue_urls():
    links = parse_series_issue_links(
        SERIES_HTML,
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29",
    )

    assert links == [
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/mr-fantastic-1",
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1",
    ]


def test_parse_issue_page_extracts_metadata_and_reviews():
    page = parse_issue_page(
        ISSUE_HTML,
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1",
    )

    assert page.title == "Challenges Of Doom: Spider-Man #1"
    assert page.publisher == "Marvel Comics"
    assert page.release_date == date(2026, 9, 9)
    assert page.writers == ("Al Ewing",)
    assert page.artists == ("David Messina",)
    assert len(page.reviews) == 3
    assert page.reviews[0].review_type == "critic"
    assert page.reviews[0].source == "Nerd Initiative"
    assert page.reviews[0].author == "Megan Nichole"
    assert page.reviews[0].score == 10
    assert page.reviews[0].full_review_url == "https://nerdinitiative.com/review"
    assert page.reviews[-1].review_type == "user"
    assert page.reviews[-1].source == "daspidaboy"


def test_score_issue_match_rewards_specific_issue_metadata():
    page = parse_issue_page(
        ISSUE_HTML,
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1",
    )
    query = ComicBookRoundupIssueQuery(
        series_title="Challenges of Doom",
        issue_title="Spider-Man",
        issue_number="1",
        publisher="Marvel Comics",
        release_date=date(2026, 9, 9),
        writers=("Al Ewing",),
        artists=("David Messina",),
    )

    score, reasons = score_issue_match(query, page)

    assert score >= 75
    assert "publisher" in reasons
    assert "strong-title" in reasons
    assert "issue-number" in reasons
    assert "release-date" in reasons
    assert "creators" in reasons


def test_score_issue_match_penalizes_wrong_issue_number():
    page = parse_issue_page(
        ISSUE_HTML,
        "https://comicbookroundup.com/comic-books/reviews/marvel-comics/challenges-of-doom-%282026%29/spider-man-1",
    )
    query = ComicBookRoundupIssueQuery(
        series_title="Challenges of Doom",
        issue_title="Mr. Fantastic",
        issue_number="2",
        publisher="Marvel Comics",
        release_date=date(2026, 9, 9),
    )

    score, reasons = score_issue_match(query, page)

    assert score < 75
    assert "issue-number-mismatch" in reasons


def test_score_issue_match_prefers_base_issue_over_same_series_special():
    query = ComicBookRoundupIssueQuery(
        series_title="Absolute Batman",
        issue_number="1",
        publisher="DC Comics",
    )
    base_page = ComicBookRoundupIssuePage(
        url="https://comicbookroundup.com/comic-books/reviews/dc-comics/absolute-batman-(2024)/1",
        title="Absolute Batman #1",
        publisher="DC Comics",
    )
    special_page = ComicBookRoundupIssuePage(
        url="https://comicbookroundup.com/comic-books/reviews/dc-comics/absolute-batman-(2024)/ark-m-special-1",
        title="Absolute Batman: Ark M Special #1",
        publisher="DC Comics",
    )

    base_score, _ = score_issue_match(query, base_page)
    special_score, _ = score_issue_match(query, special_page)

    assert base_score - special_score >= 12


def test_score_issue_match_uses_issue_title_to_disambiguate_same_issue_number():
    query = ComicBookRoundupIssueQuery(
        series_title="Avengers",
        issue_title="Armageddon",
        issue_number="4",
        publisher="Marvel Comics",
    )
    target_page = ComicBookRoundupIssuePage(
        url="https://comicbookroundup.com/comic-books/reviews/marvel-comics/avengers-armageddon-(2026)/4",
        title="Avengers: Armageddon #4",
        publisher="Marvel Comics",
    )
    plain_page = ComicBookRoundupIssuePage(
        url="https://comicbookroundup.com/comic-books/reviews/marvel-comics/avengers-(1963)/4",
        title="Avengers #4",
        publisher="Marvel Comics",
    )

    target_score, target_reasons = score_issue_match(query, target_page)
    plain_score, plain_reasons = score_issue_match(query, plain_page)

    assert target_score - plain_score >= 12
    assert "issue-title" in target_reasons
    assert "issue-title-mismatch" in plain_reasons


def test_score_issue_match_treats_short_publisher_names_as_same_publisher():
    query = ComicBookRoundupIssueQuery(
        series_title="Avengers: Armageddon",
        issue_number="4",
        publisher="Marvel",
    )
    page = ComicBookRoundupIssuePage(
        url="https://comicbookroundup.com/comic-books/reviews/marvel-comics/avengers-armageddon-(2026)/4",
        title="Avengers: Armageddon #4",
        publisher="Marvel Comics",
        reviews=(),
    )

    score, reasons = score_issue_match(query, page)

    assert score >= 75
    assert "publisher" in reasons
    assert "publisher-mismatch" not in reasons
