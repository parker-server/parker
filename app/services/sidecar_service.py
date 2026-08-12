from lxml import etree
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class SidecarService:
    @staticmethod
    def _read_text_with_fallback(path: Path) -> Optional[str]:
        """
        Read text sidecars with a small encoding fallback chain.
        Windows tools commonly emit UTF-16 or cp1252 text files.
        """
        raw = path.read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252"):
            try:
                return raw.decode(encoding).strip()
            except UnicodeDecodeError:
                continue
        return None

    @staticmethod
    def get_summary_from_disk(folder_path: Path, entity_type: str) -> Optional[str]:
        """
        Looks for [entity_type].nfo or [entity_type].txt in the folder.
        Entity type should be 'series' or 'volume'.
        Returns the summary text or None if no sidecar exists.
        """
        # 1. Try NFO (XML) first using lxml
        nfo_file = folder_path / f"{entity_type}.nfo"
        if nfo_file.exists():
            try:
                # Using lxml to match your ComicInfo.xml parser
                parser = etree.XMLParser(recover=True, remove_blank_text=True)

                # Load file bytes to avoid encoding issues
                tree = etree.fromstring(nfo_file.read_bytes(), parser)

                # Prioritize standard tags
                for tag in ["plot", "summary", "notes"]:
                    elem = tree.find(tag)
                    if elem is not None and elem.text:
                        return elem.text.strip()
            except Exception as e:
                logger.error(f"Sidecar: lxml failed to parse NFO at {nfo_file}: {e}")

        # 2. Try TXT fallback
        txt_file = folder_path / f"{entity_type}.txt"
        if txt_file.exists():
            try:
                text = SidecarService._read_text_with_fallback(txt_file)
                if text is None:
                    logger.error(f"Failed to decode TXT at {txt_file}: unsupported encoding")
                return text
            except Exception as e:
                logger.error(f"Failed to read TXT at {txt_file}: {e}")

        return None

