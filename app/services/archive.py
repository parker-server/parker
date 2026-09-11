import logging
import zipfile
import rarfile
from pathlib import Path
from typing import List, Optional
import io
import re
from app.config import settings

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.jxl', '.avif'}
IGNORE_FILENAMES = {'thumbs.db', '.ds_store', 'comicinfo.xml', '__macosx'}
IGNORE_EXTENSIONS = {'.nfo', '.sfv', '.txt', '.xml', '.db', '.ini'}
EXPLICIT_END_PAGE_RE = re.compile(r'^z+[\W_]')
EXPLICIT_COVER_RE = re.compile(r'(?:^|[\W_])(fc|cover|front|scan)(?:$|[\W_])')
TRAILING_PAGE_NUMBER_RE = re.compile(r'^(.*?)(?:[\s._-]+)?(\d+)\s*$')


def _split_archive_path(filename: str) -> tuple[str, str]:
    parts = re.split(r'([\\/])', filename)
    if len(parts) == 1:
        return "", filename

    return "".join(parts[:-1]), parts[-1]


def _normalize_page_sort_text(text: str) -> str:
    return text.lower().replace('-', '~').replace('_', '~')


def _natural_sort_parts(text: str) -> list:
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', text)]


def _priority_bucket(filename: str) -> int:
    text = filename.lower()
    if EXPLICIT_END_PAGE_RE.match(text):
        return 2
    if EXPLICIT_COVER_RE.search(text):
        return 0
    return 1


def _split_trailing_page_number(stem: str) -> tuple[str, int | None]:
    match = TRAILING_PAGE_NUMBER_RE.match(stem.strip())
    if not match:
        return stem.strip(), None

    return match.group(1).strip(), int(match.group(2))


def _page_sort_key(filename: str) -> tuple:
    """
    Sort comic archive pages while keeping likely base cover files before
    same-stem numbered interior pages.
    """
    directory, basename = _split_archive_path(filename)
    stem = Path(basename).stem
    base_stem, trailing_number = _split_trailing_page_number(stem)

    base_name = f"{directory}{base_stem}"
    normalized_base = _normalize_page_sort_text(base_name)
    normalized_full = _normalize_page_sort_text(filename)

    has_trailing_number = trailing_number is not None
    return (
        _priority_bucket(filename),
        _natural_sort_parts(normalized_base),
        1 if has_trailing_number else 0,
        trailing_number if trailing_number is not None else -1,
        _natural_sort_parts(normalized_full),
    )


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
        files = self.get_file_list()

        # Filter to valid images only
        pages = []
        for f in files:
            file_path = Path(f)
            filename_lower = file_path.name.lower()

            # Skip ignored files
            if filename_lower in IGNORE_FILENAMES:
                continue

            # Skip ignored extensions
            if file_path.suffix.lower() in IGNORE_EXTENSIONS:
                continue

            # Only include valid image files
            if file_path.suffix.lower() in IMAGE_EXTENSIONS:
                pages.append(f)

        pages.sort(key=_page_sort_key)

        return pages

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
