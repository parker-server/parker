import os
import shutil
import zipfile
import subprocess
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional, Dict, Any
from lxml import etree

from app.services.archive import ComicArchive

logger = logging.getLogger(__name__)

# --- XML Mapping Config ---
# Maps our internal snake_case keys to ComicInfo PascalCase tags
KEY_MAP = {
    'series': 'Series', 'number': 'Number', 'volume': 'Volume',
    'title': 'Title', 'summary': 'Summary', 'year': 'Year',
    'month': 'Month', 'day': 'Day', 'writer': 'Writer',
    'penciller': 'Penciller', 'inker': 'Inker', 'colorist': 'Colorist',
    'letterer': 'Letterer', 'cover_artist': 'CoverArtist', 'editor': 'Editor',
    'publisher': 'Publisher', 'imprint': 'Imprint', 'format': 'Format',
    'series_group': 'SeriesGroup', 'page_count': 'PageCount',
    'scan_information': 'ScanInformation', 'characters': 'Characters',
    'teams': 'Teams', 'locations': 'Locations', 'genre': 'Genre',
    'alternate_series': 'AlternateSeries', 'alternate_number': 'AlternateNumber',
    'story_arc': 'StoryArc', 'web': 'Web', 'age_rating': 'AgeRating',
    'lang': 'LanguageISO', 'community_rating': 'CommunityRating', 'count': 'Count'
}


def parse_comicinfo(xml_content: bytes) -> Dict[str, Any]:
    """
    Parse ComicInfo.xml and return structured data
    XSD: https://github.com/anansi-project/comicinfo/blob/main/schema/v2.0/ComicInfo.xsd
    """
    try:
        root = etree.fromstring(xml_content)

        # Helper to get text or None
        def get_text(element_name: str) -> Optional[str]:
            elem = root.find(element_name)
            return elem.text if elem is not None and elem.text else None

        # Helper to parse rating safely
        def get_rating(element_name: str) -> Optional[float]:
            text = get_text(element_name)
            if not text:
                return None
            try:
                # Handle comma decimals (e.g. "4,5") and clean whitespace
                clean_val = text.replace(',', '.').strip()
                val = float(clean_val)

                # Clamp to 0-5 range per XSD
                return max(0.0, min(5.0, val))
            except ValueError:
                return None

        return {
            # Basic info
            'series': get_text('Series'),
            'number': get_text('Number'),
            'volume': get_text('Volume'),
            'title': get_text('Title'),
            'summary': get_text('Summary'),
            'count': get_text('Count'),
            'age_rating': get_text('AgeRating'),
            'lang': get_text('LanguageISO'),
            'community_rating': get_rating('CommunityRating'),

            # Date
            'year': get_text('Year'),
            'month': get_text('Month'),
            'day': get_text('Day'),

            # Credits
            'writer': get_text('Writer'),
            'penciller': get_text('Penciller'),
            'inker': get_text('Inker'),
            'colorist': get_text('Colorist'),
            'letterer': get_text('Letterer'),
            'cover_artist': get_text('CoverArtist'),
            'editor': get_text('Editor'),

            # Publishing
            'publisher': get_text('Publisher'),
            'imprint': get_text('Imprint'),
            'format': get_text('Format'),
            'series_group': get_text('SeriesGroup'),

            # Technical
            'page_count': get_text('PageCount'),
            'scan_information': get_text('ScanInformation'),

            # Tags (these come as comma-separated in the XML)
            'characters': get_text('Characters'),
            'teams': get_text('Teams'),
            'locations': get_text('Locations'),
            'genre': get_text('Genre'),

            # Reading lists
            'alternate_series': get_text('AlternateSeries'),
            'alternate_number': get_text('AlternateNumber'),
            'story_arc': get_text('StoryArc'),

            # Web link
            'web': get_text('Web'),

            # Store full XML for future use
            'raw_xml': xml_content.decode('utf-8')
        }
    except Exception as e:
        print(f"Error parsing ComicInfo.xml: {e}")
        return {}


class MetadataService:
    def __init__(self):
        # Look for RAR executable on init
        self.rar_exe = shutil.which('rar')

    def can_write(self, file_path: str) -> bool:

        # 1. Existence Check
        if not os.path.exists(file_path):
            return False

        # 2. Permission Check (The Docker/OS check)
        if not os.access(file_path, os.W_OK):
            return False

        # 3. Format Check
        ext = file_path.lower().split('.')[-1]
        if ext == 'cbz': return True
        if ext == 'cbr' and self.rar_exe: return True

        return False

    def read_metadata(self, file_path: str) -> Dict[str, Any]:
        """
        Use ComicArchive to robustly extract metadata from any supported format.
        """
        if not os.path.exists(file_path):
            return {}

        try:
            # Use ComicArchive context manager
            with ComicArchive(Path(file_path)) as archive:
                xml_bytes = archive.get_comicinfo()

                if xml_bytes:
                    return parse_comicinfo(xml_bytes)

        except Exception as e:
            logger.error(f"Failed to read metadata from {file_path}: {e}")

        return {}


    def write_metadata(self, file_path: str, updates: Dict[str, Any]) -> bool:
        """
        1. Read existing metadata (to preserve fields we aren't changing).
        2. Merge with updates.
        3. Write back to archive.
        """
        if not self.can_write(file_path):
            raise ValueError(f"Cannot write to {file_path}. Is 'rar' installed?")

        # 1. READ: Use ComicArchive to get the raw XML bytes safely
        raw_xml = None
        try:
            with ComicArchive(Path(file_path)) as archive:
                raw_xml = archive.get_comicinfo()
        except Exception as e:
            logger.warning(f"Could not read existing XML (creating new): {e}")

        # 2. PARSE & MODIFY (In Memory)
        if raw_xml and len(raw_xml) > 0:
            try:
                parser = etree.XMLParser(remove_blank_text=True)
                root = etree.fromstring(raw_xml, parser)
            except etree.XMLSyntaxError:
                root = etree.Element("ComicInfo")
        else:
            root = etree.Element("ComicInfo")

        for key, value in updates.items():
            xml_tag = KEY_MAP.get(key, key)  # Map snake_case -> PascalCase

            # Find or Create Node
            node = root.find(xml_tag)

            if value is None or value == "":
                if node is not None:
                    root.remove(node)
            else:
                if node is None:
                    node = etree.SubElement(root, xml_tag)
                node.text = str(value)

        # 3. SERIALIZE
        new_xml_bytes = etree.tostring(
            root,
            encoding='utf-8',
            xml_declaration=True,
            pretty_print=True
        )

        # 4. WRITE (Specialized logic required as ComicArchive is Read-Only)
        self._inject_xml(file_path, new_xml_bytes)
        return True

    def _inject_xml(self, archive_path: str, xml_bytes: bytes):
        """Inject the bytes into the archive (Destructive Write)"""
        import zipfile

        with TemporaryDirectory() as temp_dir:
            xml_path = os.path.join(temp_dir, "ComicInfo.xml")
            with open(xml_path, 'wb') as f:
                f.write(xml_bytes)

            ext = archive_path.lower().split('.')[-1]
            if ext == 'cbz':
                self._write_cbz(archive_path, xml_path)
            elif ext == 'cbr':
                self._write_cbr(archive_path, xml_path)

    def _write_cbz(self, archive_path: str, xml_file: str):
        # Atomic-ish Write: Create new zip, copy all, replace XML, swap file.
        temp_zip = archive_path + ".tmp"
        try:
            with zipfile.ZipFile(archive_path, 'r') as zin:
                with zipfile.ZipFile(temp_zip, 'w') as zout:
                    # Copy all items except ComicInfo.xml
                    for item in zin.infolist():
                        if item.filename != 'ComicInfo.xml':
                            zout.writestr(item, zin.read(item.filename))

                    # Write new XML
                    zout.write(xml_file, "ComicInfo.xml")

            # Swap
            shutil.move(temp_zip, archive_path)
        except Exception as e:
            if os.path.exists(temp_zip):
                os.remove(temp_zip)
            raise e

    def _write_cbr(self, archive_path: str, xml_file: str):
        # Use 'rar' command to add/update file
        # rar a -ep -o+ archive.cbr ComicInfo.xml
        cmd = [self.rar_exe, 'a', '-ep', '-o+', archive_path, xml_file]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# Singleton instance for easy import
metadata_service = MetadataService()

