import logging
import zipfile
import rarfile
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
import io
import re
from app.config import settings

logger = logging.getLogger(__name__)

ARCHIVE_PAGE_ORDER_VERSION = 9
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.jxl', '.avif'}
IGNORE_FILENAMES = {'thumbs.db', '.ds_store', 'comicinfo.xml', '__macosx'}
IGNORE_EXTENSIONS = {'.nfo', '.sfv', '.txt', '.xml', '.db', '.ini'}
# Pages that sort last: "z_credits", "zzz scan", or tags glued to their z like "zzmm"
# and camelCase "zWraithOfSpectre". The camelCase check is case-sensitive so "Zatanna" is safe.
EXPLICIT_END_PAGE_RE = re.compile(r'^(?:(?i:z+[\W_]|zz)|z(?=[A-Z]))')
# Scanner tags wrapped in xx, like "XX-Empire-Meganubis-XX"
SCANNER_TAG_RE = re.compile(r'^xx[\W_].*[\W_]xx$', re.IGNORECASE)
EXPLICIT_COVER_RE = re.compile(
    r'(?:^|[\W_])(?:0+(?:[a-z]|[\W_]+\d+|fc|fcover|cover|cvr|front)|fc|fcover|cover|cvr|front)(?:$|[\W_])'
    # "fc" glued to a short page number, as in "scan-01fc"
    r'|[\W_]\d{1,3}(?:fc|fcover)(?:$|[\W_])'
)
INSIDE_COVER_RE = re.compile(r'(?:^|[\W_])(?:0+)?(?:ifc|ifcover|inside[\W_]+front[\W_]+cover)(?:$|[\W_])')
JOINED_COVER_RE = re.compile(r'(?:^|[\W_])(?:\d+\s*page\s+cover|joined\s+(?:cover|cvr))(?:$|[\W_])')
PREVIEW_HEADER_RE = re.compile(r'(?:preview|header)')
# "bc" may be glued to a page number, as in "p00bc"
BACK_COVER_RE = re.compile(r'(?:^|[\W_]|\d)(?:bc|bcover|back[\W_]+cover)(?:$|[\W_])')
# A wraparound cover names both sides, like "00 fc+bc", "00_FC-BC" or "00fc29bc". It is not
# a back cover, but it is not treated as a front cover either, so a separate front cover wins.
WRAPAROUND_COVER_RE = re.compile(r'(?:^|[\W_]|\d)fc[\W_\d]*(?:and[\W_]*)?bc(?:$|[\W_])')
TRAILING_PAGE_NUMBER_RE = re.compile(r'^(.*?)(?:[\s._-]+)?(\d+)\s*$')
# A leading ~ or _ is a "sort me first" marker, like "~max0019.jpg" or "_IMG.jpg"
LEADING_TILDE_SORT_PREFIX_RE = re.compile(r'(^|[\\/])[~_]+')
ZERO_PAGE_STEM_RE = re.compile(r'(?:^|[\W_])0+$')
# A run of zeros (not part of a larger number), an optional single letter, then the
# end or a separator: "000", "Batman 678-000b (variant)", "pg00a_anthony"
ZERO_PAGE_RE = re.compile(
    r'^(?P<prefix>.*(?:^|\D)0+)(?P<letter>[a-z]?)(?P<suffix>$|[\W_].*)$',
    re.IGNORECASE,
)
APPENDED_ZERO_PAGE_SUFFIX_RE = re.compile(r'^[\W_]+\d+$')
FINAL_PAGE_INDEX_RE = re.compile(r'(\d+)(?:[a-z]*)$', re.IGNORECASE)


class PageRole(Enum):
    COVER = "cover"
    LIKELY_COVER = "likely_cover"
    INTERIOR = "interior"
    BACK_MATTER = "back_matter"


PAGE_ROLE_RANK = {
    PageRole.COVER: 0,
    PageRole.LIKELY_COVER: 0,
    PageRole.INTERIOR: 1,
    PageRole.BACK_MATTER: 2,
}


@dataclass(frozen=True)
class PageSortScore:
    role: PageRole
    page_index: int | None
    cover_signal: str | None
    penalty_signals: tuple[str, ...]
    normalized_base_parts: tuple
    has_trailing_page_number: bool
    trailing_page_number: int | None
    normalized_full_parts: tuple
    archive_index: int = 0

    def sort_key(self) -> tuple:
        return (
            PAGE_ROLE_RANK[self.role],
            self.normalized_base_parts,
            1 if self.has_trailing_page_number else 0,
            self.page_index if self.page_index is not None else -1,
            self.trailing_page_number if self.trailing_page_number is not None else -1,
            self.normalized_full_parts,
            len(self.penalty_signals),
            self.archive_index,
        )


def _split_archive_path(filename: str) -> tuple[str, str]:
    parts = re.split(r'([\\/])', filename)
    if len(parts) == 1:
        return "", filename

    return "".join(parts[:-1]), parts[-1]


def _normalize_page_sort_text(text: str) -> str:
    text = LEADING_TILDE_SORT_PREFIX_RE.sub(_normalize_leading_tilde_sort_prefix, text.lower())
    text = re.sub(r'[-_](?=\d)', '!', text)
    return text.replace('-', '~').replace('_', '~')


def _normalize_leading_tilde_sort_prefix(match: re.Match) -> str:
    separator = match.group(1)
    return f"{separator}{' ' * (len(match.group(0)) - len(separator))}"


def _natural_sort_parts(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', text)]


def _natural_sort_tuple(text: str) -> tuple:
    return tuple(_natural_sort_parts(text))


def _page_stem_key(filename: str) -> str:
    directory, basename = _split_archive_path(filename)
    return f"{directory}{Path(basename).stem.strip()}"


def _has_unprefixed_twin(filename: str, page_stems: set[str] | None) -> bool:
    if page_stems is None:
        return False

    directory, basename = _split_archive_path(filename)
    stem = Path(basename).stem.strip()
    if not stem.startswith("_"):
        return False

    return f"{directory}{stem[1:]}" in page_stems


def _zero_page_parts(stem_key: str) -> tuple[str, bool] | None:
    """
    For a zero page like "Batman 678-000" or "Batman 678-000b (variant)", return
    the text up to and including the zeros, and whether a single letter follows them.
    """
    directory, stem = _split_archive_path(stem_key)
    match = ZERO_PAGE_RE.match(stem.strip())
    if not match:
        return None
    return f"{directory}{match.group('prefix')}".lower(), bool(match.group("letter"))


@lru_cache(maxsize=64)
def _zero_page_prefixes(page_stems: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Prefixes of the bare zero pages and of the zero-letter pages in one archive."""
    bare = set()
    lettered = set()
    for stem_key in page_stems:
        parts = _zero_page_parts(stem_key)
        if parts:
            prefix, has_letter = parts
            (lettered if has_letter else bare).add(prefix)
    return frozenset(bare), frozenset(lettered)


def _has_zero_letter_twin(filename: str, page_stems: set[str] | None) -> bool:
    """A bare zero page ("-000") next to a lettered one ("-000b (variant)") with the same prefix."""
    if page_stems is None:
        return False

    parts = _zero_page_parts(_page_stem_key(filename))
    if not parts or parts[1]:
        return False
    return parts[0] in _zero_page_prefixes(frozenset(page_stems))[1]


def _has_bare_zero_page_twin(filename: str, page_stems: set[str] | None) -> bool:
    """A lettered zero page ("-000b (variant)") next to a bare one ("-000") with the same prefix."""
    if page_stems is None:
        return False

    parts = _zero_page_parts(_page_stem_key(filename))
    if not parts or not parts[1]:
        return False
    return parts[0] in _zero_page_prefixes(frozenset(page_stems))[0]


def _has_appended_zero_page_twin(filename: str, page_stems: set[str] | None) -> bool:
    if page_stems is None:
        return False

    directory, basename = _split_archive_path(filename)
    stem = Path(basename).stem.strip()
    if not ZERO_PAGE_STEM_RE.search(stem):
        return False

    stem_key = f"{directory}{stem}".lower()
    return any(
        page_stem.lower().startswith(stem_key)
        and APPENDED_ZERO_PAGE_SUFFIX_RE.match(page_stem.lower()[len(stem_key):]) is not None
        for page_stem in page_stems
    )


def _page_penalty_signals(
    text: str,
    appended_page_number_variant: bool = False,
    appended_page_zero_variant: bool = False,
    zero_letter_page_variant: bool = False,
) -> tuple[str, ...]:
    penalties = []
    if INSIDE_COVER_RE.search(text):
        penalties.append("inside_front_cover")
    if JOINED_COVER_RE.search(text):
        penalties.append("joined_cover")
    if PREVIEW_HEADER_RE.search(text):
        penalties.append("preview_or_header")
    if WRAPAROUND_COVER_RE.search(text):
        penalties.append("wraparound_cover")
    elif BACK_COVER_RE.search(text):
        penalties.append("back_cover")
    if appended_page_number_variant:
        penalties.append("appended_page_number_variant")
    if appended_page_zero_variant:
        penalties.append("appended_page_zero_variant")
    if zero_letter_page_variant:
        penalties.append("zero_letter_page_variant")
    return tuple(penalties)


def _cover_signal(filename: str, text: str, page_stems: set[str] | None, penalty_signals: tuple[str, ...]) -> str | None:
    if "inside_front_cover" in penalty_signals:
        return None

    explicit_cover_blockers = {
        "appended_page_number_variant",
        "appended_page_zero_variant",
        "joined_cover",
        "wraparound_cover",
        "zero_letter_page_variant",
    }
    if EXPLICIT_COVER_RE.search(text) and not explicit_cover_blockers.intersection(penalty_signals):
        return "explicit_cover_token"
    if _has_zero_letter_twin(filename, page_stems):
        return "bare_zero_with_zero_letter_twin"
    if _has_appended_zero_page_twin(filename, page_stems):
        return "bare_zero_with_appended_variant"
    if _has_unprefixed_twin(filename, page_stems):
        return "leading_underscore_twin"
    return None


def _is_end_page(filename: str) -> bool:
    _, basename = _split_archive_path(filename)
    return bool(EXPLICIT_END_PAGE_RE.match(basename) or SCANNER_TAG_RE.match(Path(basename).stem))


def _page_role(cover_signal: str | None, filename: str, penalty_signals: tuple[str, ...]) -> PageRole:
    if _is_end_page(filename) or "back_cover" in penalty_signals:
        return PageRole.BACK_MATTER
    if cover_signal == "explicit_cover_token":
        return PageRole.COVER
    if cover_signal:
        return PageRole.LIKELY_COVER
    return PageRole.INTERIOR


def _detect_page_index(stem: str, trailing_number: int | None) -> int | None:
    if trailing_number is not None:
        return trailing_number

    match = FINAL_PAGE_INDEX_RE.search(stem.strip())
    if not match:
        return None

    return int(match.group(1))


def _page_sort_score(filename: str, page_stems: set[str] | None = None, archive_index: int = 0) -> PageSortScore:
    """
    Build a named, debug-friendly page ordering score.

    Role and signal fields explain why a page is cover-priority, interior, or
    back matter. Natural filename fields remain the final tie breakers so the
    sorter still follows archive naming conventions whenever role signals tie.
    """
    directory, basename = _split_archive_path(filename)
    # Cover words in folder names (like "Fathom 1 0f 3/") would mark every page
    text = basename.lower()
    stem = Path(basename).stem
    base_stem, trailing_number = _split_trailing_page_number(stem)
    appended_page_number_variant = False
    appended_page_zero_variant = False
    zero_letter_page_variant = _has_bare_zero_page_twin(filename, page_stems)

    if trailing_number is not None and page_stems is not None:
        candidate_base = f"{directory}{base_stem}"
        if candidate_base in page_stems:
            appended_page_number_variant = True
            appended_page_zero_variant = ZERO_PAGE_STEM_RE.search(base_stem) is not None
        else:
            base_stem = stem.strip()
            trailing_number = None

    base_name = f"{directory}{base_stem}"
    penalty_signals = _page_penalty_signals(
        text,
        appended_page_number_variant,
        appended_page_zero_variant,
        zero_letter_page_variant,
    )
    cover_signal = _cover_signal(filename, text, page_stems, penalty_signals)

    return PageSortScore(
        role=_page_role(cover_signal, filename, penalty_signals),
        page_index=_detect_page_index(stem, trailing_number),
        cover_signal=cover_signal,
        penalty_signals=penalty_signals,
        normalized_base_parts=_natural_sort_tuple(_normalize_page_sort_text(base_name)),
        has_trailing_page_number=trailing_number is not None,
        trailing_page_number=trailing_number,
        normalized_full_parts=_natural_sort_tuple(_normalize_page_sort_text(filename)),
        archive_index=archive_index,
    )


def _split_trailing_page_number(stem: str) -> tuple[str, int | None]:
    match = TRAILING_PAGE_NUMBER_RE.match(stem.strip())
    if not match:
        return stem.strip(), None

    return match.group(1).strip(), int(match.group(2))


def _page_sort_key(filename: str, page_stems: set[str] | None = None, archive_index: int = 0) -> tuple:
    """
    Sort comic archive pages using a named score so cover decisions are easier
    to inspect when archive naming gets strange.
    """
    return _page_sort_score(filename, page_stems, archive_index).sort_key()


def sort_pages(files: List[str]) -> List[str]:
    """Filter an archive's file list to image pages and sort them, cover first."""
    pages = []
    for f in files:
        file_path = Path(f)
        filename_lower = file_path.name.lower()

        # Skip ignored files
        if filename_lower in IGNORE_FILENAMES:
            continue

        # Skip macOS resource forks ("._page.jpg", often under "__MACOSX/"): they are not images
        if filename_lower.startswith("._") or "__macosx" in (part.lower() for part in file_path.parts):
            continue

        # Skip ignored extensions
        if file_path.suffix.lower() in IGNORE_EXTENSIONS:
            continue

        # Only include valid image files
        if file_path.suffix.lower() in IMAGE_EXTENSIONS:
            pages.append(f)

    page_stems = {_page_stem_key(page) for page in pages}
    indexed_pages = list(enumerate(pages))
    indexed_pages.sort(key=lambda item: _page_sort_key(item[1], page_stems, item[0]))

    return [page for _, page in indexed_pages]


# Import the rarfile configuration
import rarfile
try:
    rarfile.UNRAR_TOOL = settings.unrar_path
except:
    pass

try:
    import py7zr
    CB7_SUPPORT = True
except ImportError:
    CB7_SUPPORT = False
    logger.warning("Warning: py7zr not installed. CB7 support disabled.")

class ComicArchive:
    """Unified interface for CBZ, CBR, and CB7 archives"""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.extension = filepath.suffix.lower()
        self.archive = self._open_archive()

    def _open_archive(self):
        """Open the appropriate archive handler with fallback for mislabeled extensions"""
        # 1. Try to detect the format based on file signatures first (most reliable)
        if zipfile.is_zipfile(self.filepath):
            if self.extension != ".cbz":
                logger.warning(f"Mislabeled archive: {self.filepath.name} is ZIP but labeled as {self.extension}")
                self.extension = ".cbz"
            return zipfile.ZipFile(self.filepath)

        if rarfile.is_rarfile(self.filepath):
            if self.extension != ".cbr":
                logger.warning(f"Mislabeled archive: {self.filepath.name} is RAR but labeled as {self.extension}")
                self.extension = ".cbr"
            return rarfile.RarFile(self.filepath)

        # 2. Fall back to extension-based opening if detection failed
        # This provides standard library error messages if the file is corrupted
        if self.extension == ".cbz":
            return zipfile.ZipFile(self.filepath)
        elif self.extension == ".cbr":
            return rarfile.RarFile(self.filepath)
        elif self.extension == ".cb7":
            if not CB7_SUPPORT:
                raise ValueError("CB7 support not available. Install py7zr package.")
            return py7zr.SevenZipFile(self.filepath)
        else:
            raise ValueError(f"Unsupported format: {self.extension}")

    def get_file_list(self) -> List[str]:
        """Get list of files in archive"""
        if self.extension == ".cbz":
            return self.archive.namelist()
        elif self.extension == ".cbr":
            return self.archive.namelist()
        elif self.extension == ".cb7":
            return self.archive.getnames()

    def get_pages(self) -> List[str]:
        """Get sorted list of image files (pages) - filter out non-images"""
        return sort_pages(self.get_file_list())

    def read_file(self, filename: str) -> bytes:
        """Read a specific file from the archive"""
        if self.extension == ".cbz":
            return self.archive.read(filename)
        elif self.extension == ".cbr":
            return self.archive.read(filename)
        elif self.extension == ".cb7":
            return self.archive.read([filename])[filename].read()

    def get_comicinfo(self) -> Optional[bytes]:
        """Extract ComicInfo.xml if it exists"""
        files = self.get_file_list()
        comicinfo = next((f for f in files if f.lower() == "comicinfo.xml"), None)

        if comicinfo:
            return self.read_file(comicinfo)
        return None

    def close(self):
        """Close the archive"""
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
