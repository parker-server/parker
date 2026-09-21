from unittest.mock import MagicMock, patch
from pathlib import Path
from app.services.archive import ComicArchive, PageRole, _page_sort_score

def test_comic_archive_get_pages_filtering():
    """Test that non-images and ignored files are filtered out."""
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))
        
        archive.get_file_list = MagicMock(return_value=[
            "01.jpg",
            "02.PNG",
            "03.WEBP",
            "04.jxl",
            "05.avif",
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
        assert pages == ["01.jpg", "02.PNG", "03.WEBP", "04.jxl", "05.avif"]

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


def test_comic_archive_sort_pages_prefers_issue_base_image_before_numbered_page():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            " Lord of the Ultra-Realms 01 0001.jpg",
            " Lord of the Ultra-Realms 01 0002.jpg",
            " Lord of the Ultra-Realms 01 .jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            " Lord of the Ultra-Realms 01 .jpg",
            " Lord of the Ultra-Realms 01 0001.jpg",
            " Lord of the Ultra-Realms 01 0002.jpg",
        ]


def test_comic_archive_sort_pages_prefers_same_stem_cover_before_appended_page_number():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "dc_gods02.jpg",
            "dc_gods01.jpg",
            "dc_gods.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "dc_gods.jpg",
            "dc_gods01.jpg",
            "dc_gods02.jpg",
        ]


def test_comic_archive_sort_pages_treats_zero_fc_token_as_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "hawk_&_dove.v02_a01.imbie.01.jpg",
            "hawk_&_dove.v02_a01.imbie.02.jpg",
            "hawk_&_dove.v02_a01.imbie.00fc.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "hawk_&_dove.v02_a01.imbie.00fc.jpg",
            "hawk_&_dove.v02_a01.imbie.01.jpg",
            "hawk_&_dove.v02_a01.imbie.02.jpg",
        ]


def test_comic_archive_sort_pages_treats_cvr_abbreviation_as_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Star Blazers v1 #1 002.jpg",
            "Star Blazers v1 #1 001 joined cvr.jpg",
            "Star Blazers v1 #1 000 cvr.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Star Blazers v1 #1 000 cvr.jpg",
            "Star Blazers v1 #1 001 joined cvr.jpg",
            "Star Blazers v1 #1 002.jpg",
        ]


def test_comic_archive_sort_pages_treats_zero_prefixed_cover_word_as_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "02.jpg",
            "00Cover.jpg",
            "01.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "00Cover.jpg",
            "01.jpg",
            "02.jpg",
        ]


def test_comic_archive_sort_pages_treats_zero_letter_page_as_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Justice Machine(Comico) 01-01.jpg",
            "Justice Machine(Comico) 01-00b.jpg",
            "Justice Machine(Comico) 01-00a.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Justice Machine(Comico) 01-00a.jpg",
            "Justice Machine(Comico) 01-00b.jpg",
            "Justice Machine(Comico) 01-01.jpg",
        ]


def test_comic_archive_sort_pages_treats_zero_page_with_total_suffix_as_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Untitled-Scanned-02.jpg",
            "Untitled-Scanned-01-36.jpg",
            "Untitled-Scanned-00-36.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Untitled-Scanned-00-36.jpg",
            "Untitled-Scanned-01-36.jpg",
            "Untitled-Scanned-02.jpg",
        ]


def test_comic_archive_sort_pages_treats_zero_c_as_cover_without_promoting_ifc():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Elementals_Sex_Special_004-01.jpg",
            "Elementals_Sex_Special_004-00IFC.jpg",
            "Elementals_Sex_Special_004-00C.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Elementals_Sex_Special_004-00C.jpg",
            "Elementals_Sex_Special_004-00IFC.jpg",
            "Elementals_Sex_Special_004-01.jpg",
        ]


def test_comic_archive_sort_pages_prefers_leading_underscore_twin_cover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Robotech Macross Saga 01-00ifc.jpg",
            "Robotech Macross Saga 01-00fcbc.jpg",
            "_Robotech Macross Saga 01-00fcbc.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "_Robotech Macross Saga 01-00fcbc.jpg",
            "Robotech Macross Saga 01-00fcbc.jpg",
            "Robotech Macross Saga 01-00ifc.jpg",
        ]


def test_comic_archive_sort_pages_prefers_bare_zero_page_before_zero_letter_join():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "HybridsSpecial001-00A.jpg",
            "HybridsSpecial001-00.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "HybridsSpecial001-00.jpg",
            "HybridsSpecial001-00A.jpg",
        ]


def test_comic_archive_sort_pages_prefers_bare_zero_page_before_titled_zero_letter_variant():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "TarzanJohnCarter1-00a Red Awakenings.jpg",
            "TarzanJohnCarter1-00 Red Awakenings.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "TarzanJohnCarter1-00 Red Awakenings.jpg",
            "TarzanJohnCarter1-00a Red Awakenings.jpg",
        ]


def test_comic_archive_sort_pages_prefers_bare_zero_page_before_appended_zero_variant():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Earth4-V2-001-00-35.jpg",
            "Earth4-V2-001-00.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Earth4-V2-001-00.jpg",
            "Earth4-V2-001-00-35.jpg",
        ]


def test_comic_archive_sort_pages_prefers_separator_number_before_alpha_suffix():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "WayofRat23-01.jpg",
            "WayofRat23NegWarPreviewHeader.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "WayofRat23-01.jpg",
            "WayofRat23NegWarPreviewHeader.jpg",
        ]


def test_comic_archive_sort_pages_does_not_promote_two_page_cover_over_base_page():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "The First - 005 Pg00z (2 page cover).jpg",
            "The First - 005 Pg00.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "The First - 005 Pg00.jpg",
            "The First - 005 Pg00z (2 page cover).jpg",
        ]


def test_comic_archive_sort_pages_prefers_fcover_over_ifcover():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "chimera_04_pg_00a_ifcover_(shinter).jpg",
            "chimera_04_pg_00_fcover_(shinter).jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "chimera_04_pg_00_fcover_(shinter).jpg",
            "chimera_04_pg_00a_ifcover_(shinter).jpg",
        ]


def test_page_sort_score_exposes_named_cover_signals():
    page_stems = {
        "HybridsSpecial001-00",
        "HybridsSpecial001-00A",
        "chimera_04_pg_00_fcover_(shinter)",
        "chimera_04_pg_00a_ifcover_(shinter)",
        "The First - 005 Pg00",
        "The First - 005 Pg00z (2 page cover)",
        "WayofRat23-01",
        "WayofRat23NegWarPreviewHeader",
        "Earth4-V2-001-00",
        "Earth4-V2-001-00-35",
        "TarzanJohnCarter1-00 Red Awakenings",
        "TarzanJohnCarter1-00a Red Awakenings",
    }

    bare_zero = _page_sort_score("HybridsSpecial001-00.jpg", page_stems)
    zero_letter = _page_sort_score("HybridsSpecial001-00A.jpg", page_stems)
    bare_zero_with_titled_variant = _page_sort_score("TarzanJohnCarter1-00 Red Awakenings.jpg", page_stems)
    titled_zero_letter_variant = _page_sort_score("TarzanJohnCarter1-00a Red Awakenings.jpg", page_stems)
    bare_zero_with_appended_variant = _page_sort_score("Earth4-V2-001-00.jpg", page_stems)
    appended_zero_variant = _page_sort_score("Earth4-V2-001-00-35.jpg", page_stems)
    front_cover = _page_sort_score("chimera_04_pg_00_fcover_(shinter).jpg", page_stems)
    inside_front_cover = _page_sort_score("chimera_04_pg_00a_ifcover_(shinter).jpg", page_stems)
    joined_cover = _page_sort_score("The First - 005 Pg00z (2 page cover).jpg", page_stems)
    preview_header = _page_sort_score("WayofRat23NegWarPreviewHeader.jpg", page_stems)

    assert bare_zero.role == PageRole.LIKELY_COVER
    assert bare_zero.cover_signal == "bare_zero_with_zero_letter_twin"
    assert bare_zero.page_index == 0
    assert bare_zero.sort_key() < zero_letter.sort_key()

    assert bare_zero_with_titled_variant.role == PageRole.LIKELY_COVER
    assert bare_zero_with_titled_variant.cover_signal == "bare_zero_with_zero_letter_twin"
    assert titled_zero_letter_variant.role == PageRole.INTERIOR
    assert titled_zero_letter_variant.cover_signal is None
    assert "zero_letter_page_variant" in titled_zero_letter_variant.penalty_signals
    assert bare_zero_with_titled_variant.sort_key() < titled_zero_letter_variant.sort_key()

    assert bare_zero_with_appended_variant.role == PageRole.LIKELY_COVER
    assert bare_zero_with_appended_variant.cover_signal == "bare_zero_with_appended_variant"
    assert appended_zero_variant.role == PageRole.INTERIOR
    assert appended_zero_variant.cover_signal is None
    assert "appended_page_zero_variant" in appended_zero_variant.penalty_signals
    assert bare_zero_with_appended_variant.sort_key() < appended_zero_variant.sort_key()

    assert front_cover.role == PageRole.COVER
    assert front_cover.cover_signal == "explicit_cover_token"
    assert inside_front_cover.role == PageRole.INTERIOR
    assert inside_front_cover.cover_signal is None
    assert "inside_front_cover" in inside_front_cover.penalty_signals

    assert joined_cover.role == PageRole.INTERIOR
    assert "joined_cover" in joined_cover.penalty_signals
    assert preview_header.role == PageRole.INTERIOR
    assert "preview_or_header" in preview_header.penalty_signals
