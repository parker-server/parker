from dataclasses import dataclass, field
from typing import Optional

from lxml import etree


class CBLParseError(Exception):
    """Raised when a .cbl file is not well-formed XML."""


@dataclass
class CBLEntry:
    """One <Book> entry from a CBL file, as raw (unmatched) strings."""
    series: Optional[str] = None
    number: Optional[str] = None
    volume: Optional[str] = None
    year: Optional[str] = None
    format: Optional[str] = None


@dataclass
class CBLParseResult:
    name: str
    description: Optional[str] = None
    entries: list[CBLEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _hardened_xml_parser() -> etree.XMLParser:
    # CBL files can arrive via upload, URL import, or a third-party catalog --
    # unlike ComicInfo.xml (which only ever comes from the user's own archives),
    # so we parse defensively: no external entity resolution.
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def parse_cbl(xml_content: bytes, filename_stem: str) -> CBLParseResult:
    """
    Parse a ComicRack-style .cbl file (XML: <ReadingList><Name/><Books><Book .../></Books></ReadingList>).

    Unsupported/unknown attributes (Id, Database, matchers, etc.) are ignored.
    Malformed XML raises CBLParseError; anything else recoverable is reported as
    a warning on the result rather than raised, so one bad file doesn't need to
    abort an entire import/scan batch.
    """
    try:
        root = etree.fromstring(xml_content, parser=_hardened_xml_parser())
    except etree.XMLSyntaxError as exc:
        raise CBLParseError(f"Malformed CBL XML: {exc}") from exc

    warnings: list[str] = []

    name_el = root.find("Name")
    name = name_el.text.strip() if name_el is not None and name_el.text else ""
    if not name:
        name = filename_stem

    summary_el = root.find("Summary")
    description = summary_el.text.strip() if summary_el is not None and summary_el.text else None
    description = description or None

    books_el = root.find("Books")
    book_elements = books_el.findall("Book") if books_el is not None else root.findall("Book")

    entries: list[CBLEntry] = []
    for book_el in book_elements:
        series = book_el.get("Series")
        number = book_el.get("Number")
        volume = book_el.get("Volume")
        year = book_el.get("Year")
        fmt = book_el.get("Format")

        if not series and not number:
            warnings.append("Skipped a <Book> entry with no Series or Number attribute")
            continue

        entries.append(CBLEntry(series=series, number=number, volume=volume, year=year, format=fmt))

    if not entries:
        warnings.append("CBL file contains no usable <Book> entries")

    return CBLParseResult(name=name, description=description, entries=entries, warnings=warnings)
