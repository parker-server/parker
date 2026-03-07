from unittest.mock import MagicMock, patch
from pathlib import Path
from app.services.archive import ComicArchive

def test_comic_archive_get_pages_filtering():
    """Test that non-images and ignored files are filtered out."""
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))
        
        archive.get_file_list = MagicMock(return_value=[
            "01.jpg",
            "02.PNG",
            "03.WEBP",
            "thumbs.db",
            "Thumbs.db",
            "ComicInfo.xml",
            "__MACOSX",
            "info.txt",
            "release.nfo",
            "document.pdf"
        ])
        
        pages = archive.get_pages()
        
        # Only valid images should remain, sorted naturally
        assert pages == ["01.jpg", "02.PNG", "03.WEBP"]

def test_comic_archive_sort_pages_priority():
    """Test the priority bucketing (covers first, z-pages last)."""
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))
        
        archive.get_file_list = MagicMock(return_value=[
            "02.jpg",
            "z.sig.fÆRiE-scan.jpg",
            "cover.jpg",
            "z_credit.jpg",
            "01.jpg",
            "fc.jpg",
            "front.jpg",
            "zz_promo.jpg",
            "scan.jpg",
            "00.jpg"
        ])
        
        pages = archive.get_pages()
        
        expected_pages = [
            "cover.jpg",
            "fc.jpg",
            "front.jpg",
            "scan.jpg",
            "00.jpg",
            "01.jpg",
            "02.jpg",
            "z.sig.fÆRiE-scan.jpg",
            "zz_promo.jpg",
            "z_credit.jpg"
        ]
        assert pages == expected_pages

def test_comic_archive_sort_pages_separators():
    """Test the separator hack where letters sort before symbols."""
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))
        
        archive.get_file_list = MagicMock(return_value=[
            "c01-b.jpg",
            "c01a.jpg"
        ])
        
        pages = archive.get_pages()
        
        # 'c01a' comes before 'c01-'
        assert pages == ["c01a.jpg", "c01-b.jpg"]

def test_comic_archive_sort_pages_complex_names():
    """Test edge cases for explicit covers and non-covers."""
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))
        
        archive.get_file_list = MagicMock(return_value=[
            "cover01.jpg",   # Not an explicit cover (01 is word char)
            "00_cover.jpg",  # Explicit cover
            "01.jpg",
            "scan02.jpg",    # Not explicit
            "_scan.jpg",     # Explicit
            "my_front_page.jpg" # Explicit
        ])
        
        pages = archive.get_pages()
        
        # Explicit covers: 00_cover.jpg, _scan.jpg, my_front_page.jpg
        # The rest are standard priority
        expected = [
            "00_cover.jpg", 
            "my_front_page.jpg", 
            "_scan.jpg", 
            "01.jpg", 
            "cover01.jpg", 
            "scan02.jpg"
        ]
        
        assert pages == expected
