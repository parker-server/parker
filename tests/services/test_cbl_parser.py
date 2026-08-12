from pathlib import Path

import pytest

from app.services.cbl_parser import CBLParseError, parse_cbl

FIXTURES = Path(__file__).parent.parent / "fixtures" / "cbl"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_parse_valid_cbl_reads_name_summary_and_ordered_entries():
    result = parse_cbl(_load("valid.cbl"), filename_stem="valid")

    assert result.name == "Infinity Gauntlet"
    assert result.description == "The original Infinity Gauntlet crossover reading order."
    assert result.warnings == []
    assert [e.series for e in result.entries] == ["Silver Surfer", "Infinity Gauntlet", "Infinity Gauntlet"]
    assert [e.number for e in result.entries] == ["34", "1", "2"]


def test_parse_falls_back_to_filename_stem_when_name_missing():
    result = parse_cbl(_load("missing_name.cbl"), filename_stem="missing_name")

    assert result.name == "missing_name"
    assert len(result.entries) == 1


def test_parse_preserves_half_issue_glyph_from_numeric_entity():
    result = parse_cbl(_load("half_issue.cbl"), filename_stem="half_issue")

    assert [e.number for e in result.entries] == ["1", "½"]


def test_parse_malformed_xml_raises_cbl_parse_error():
    with pytest.raises(CBLParseError):
        parse_cbl(_load("malformed.cbl"), filename_stem="malformed")


def test_parse_ignores_unknown_fields_and_nested_matcher():
    result = parse_cbl(_load("unknown_fields.cbl"), filename_stem="unknown_fields")

    assert result.name == "Unknown Fields Test"
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.series == "Silver Surfer"
    assert entry.number == "34"
    assert entry.volume == "1987"
    assert entry.year == "1990"
    assert entry.format == "TPB"


def test_parse_warns_and_skips_book_with_no_identifying_attributes():
    xml = b"""<?xml version="1.0"?>
    <ReadingList>
      <Name>No Identity</Name>
      <Books>
        <Book Format="TPB" />
      </Books>
    </ReadingList>"""

    result = parse_cbl(xml, filename_stem="no_identity")

    assert result.entries == []
    assert any("no Series or Number" in w for w in result.warnings)
    assert any("no usable" in w for w in result.warnings)


def test_parse_hardened_parser_does_not_expand_internal_entity():
    xml = b"""<?xml version="1.0"?>
    <!DOCTYPE ReadingList [
      <!ENTITY xxe "INJECTED">
    ]>
    <ReadingList>
      <Name>&xxe;</Name>
      <Books><Book Series="A" Number="1" /></Books>
    </ReadingList>"""

    result = parse_cbl(xml, filename_stem="xxe_test")

    assert result.name != "INJECTED"
