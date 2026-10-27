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
        
        # "scan" is a scanner tag, not a cover word, so scan.jpg is an ordinary page
        expected_pages = [
            "cover.jpg",
            "fc.jpg",
            "front.jpg",
            "00.jpg",
            "01.jpg",
            "02.jpg",
            "scan.jpg",
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
            "_scan.jpg",     # Not explicit: scan is a scanner tag word
            "my_front_page.jpg" # Explicit
        ])
        
        pages = archive.get_pages()
        
        # Explicit covers: 00_cover.jpg, my_front_page.jpg
        # The rest are standard priority
        expected = [
            "00_cover.jpg", 
            "my_front_page.jpg", 
            "01.jpg", 
            "_scan.jpg",
            "cover01.jpg", 
            "scan02.jpg",
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


def test_comic_archive_sort_pages_sends_z_prefixed_scan_page_to_end_inside_folder():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Fantastic Four Unplugged #1/zzz A Comixbear Scan.jpg",
            "Fantastic Four Unplugged #1/Fantastic Four Unplugged #1 {Comixbear}-02.jpg",
            "Fantastic Four Unplugged #1/Fantastic Four Unplugged #1 {Comixbear}-01.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Fantastic Four Unplugged #1/Fantastic Four Unplugged #1 {Comixbear}-01.jpg",
            "Fantastic Four Unplugged #1/Fantastic Four Unplugged #1 {Comixbear}-02.jpg",
            "Fantastic Four Unplugged #1/zzz A Comixbear Scan.jpg",
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


def test_comic_archive_sort_pages_preserves_leading_tilde_page_prefix():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Santa-DCP.jpg",
            "~max0018.jpg",
            "~max0017.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "~max0017.jpg",
            "~max0018.jpg",
            "Santa-DCP.jpg",
        ]


def test_comic_archive_sort_pages_prefers_base_issue_image_before_appended_page_variant():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "Nemesis 07_0002.jpg",
            "Nemesis 07.jpg",
        ])

        pages = archive.get_pages()

        assert pages == [
            "Nemesis 07.jpg",
            "Nemesis 07_0002.jpg",
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
        "~max0017",
        "Santa-DCP",
        "Nemesis 07",
        "Nemesis 07_0002",
    }

    bare_zero = _page_sort_score("HybridsSpecial001-00.jpg", page_stems)
    zero_letter = _page_sort_score("HybridsSpecial001-00A.jpg", page_stems)
    bare_zero_with_titled_variant = _page_sort_score("TarzanJohnCarter1-00 Red Awakenings.jpg", page_stems)
    titled_zero_letter_variant = _page_sort_score("TarzanJohnCarter1-00a Red Awakenings.jpg", page_stems)
    bare_zero_with_appended_variant = _page_sort_score("Earth4-V2-001-00.jpg", page_stems)
    appended_zero_variant = _page_sort_score("Earth4-V2-001-00-35.jpg", page_stems)
    leading_tilde_page = _page_sort_score("~max0017.jpg", page_stems)
    unprefixed_extra = _page_sort_score("Santa-DCP.jpg", page_stems)
    base_issue_image = _page_sort_score("Nemesis 07.jpg", page_stems)
    appended_page_variant = _page_sort_score("Nemesis 07_0002.jpg", page_stems)
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

    assert leading_tilde_page.sort_key() < unprefixed_extra.sort_key()

    assert appended_page_variant.role == PageRole.INTERIOR
    assert appended_page_variant.cover_signal is None
    assert "appended_page_number_variant" in appended_page_variant.penalty_signals
    assert base_issue_image.sort_key() < appended_page_variant.sort_key()

    assert front_cover.role == PageRole.COVER
    assert front_cover.cover_signal == "explicit_cover_token"
    assert inside_front_cover.role == PageRole.INTERIOR
    assert inside_front_cover.cover_signal is None
    assert "inside_front_cover" in inside_front_cover.penalty_signals

    assert joined_cover.role == PageRole.INTERIOR
    assert "joined_cover" in joined_cover.penalty_signals
    assert preview_header.role == PageRole.INTERIOR
    assert "preview_or_header" in preview_header.penalty_signals


def test_comic_archive_sort_pages_sends_xx_wrapped_scanner_tags_to_end():
    archives = [
        ["Xx-MegaXMan-xX.JPG", "X-Man_31_0001.jpg", "X-Man_31_0000.jpg"],
        ["X-Factor v1 084/XX-Empire-Meganubis-XX.JPG", "X-Factor v1 084/X-Factor v1 084-000.jpg"],
        ["XX-Logo-XX.JPG", "X-men forever 1_0001.jpg"],
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True),          patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files in archives:
            archive.get_file_list = MagicMock(return_value=files)

            pages = archive.get_pages()

            assert Path(pages[-1]).stem.lower().startswith("xx")
            assert not Path(pages[0]).stem.lower().startswith("xx")


def test_comic_archive_sort_pages_keeps_x_titles_that_are_not_tags():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=["Xx-Factor 01.jpg", "Xx-Factor 02.jpg"])

        assert archive.get_pages() == ["Xx-Factor 01.jpg", "Xx-Factor 02.jpg"]


def test_comic_archive_sort_pages_prefers_bare_zero_page_over_lettered_variant_with_other_text():
    archives = [
        ["Batman 678-000b (Tony Daniel variant).jpg", "Batman 678-001.jpg", "Batman 678-000.jpg"],
        ["action388-00a horse murals.jpg", "action388-01.jpg", "action388-00 puzzle of the wild world.jpg"],
        ["Elektra_#1_pg00a_anthony.jpg", "Elektra_#1_pg01_anthony.jpg", "Elektra_#1_pg00_anthony.jpg"],
        ["n-MisterX-10-00a-01.jpg", "n-MisterX-10-01.jpg", "n-MisterX-10-00.jpg"],
        ["rex_mundi_06_pg_00b_summary_(sd).jpg", "rex_mundi_06_pg_01_(sd).jpg", "rex_mundi_06_pg_00_fc_(sd).jpg"],
        # Different numbers of zeros still pair up
        ["The Sentry - Hulk (2001) 00q.png", "The Sentry - Hulk (2001) 002.png", "The Sentry - Hulk (2001) 000.png"],
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[0] == files[2]


def test_comic_archive_sort_pages_sends_glued_z_tags_and_scan_tags_to_end():
    archives = [
        ["zzmm.jpg", "GA & BC 011 Page 001.jpg", "GA & BC 011 Page 002.jpg"],
        ["zzZdizzylocke.jpg", "GA & BC 013 Page 001.jpg"],
        ["zWraithOfSpectre02_00a.jpg", "WraithOfSpectre02_00.jpg", "WraithOfSpectre02_01.jpg"],
        ["Showcase 032/zzztitansfan scan.jpg", "Showcase 032/Showcase-032_01.jpg"],
        ["Zombie Golfer Scan 09.jpg", "House of Mystery 298 0001.jpg"],
        ["scan.jpg", "Kamandi053-00.jpg", "Kamandi053-01.jpg"],
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[-1] == files[0]


def test_comic_archive_sort_pages_keeps_z_titles_that_are_not_tags():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=["Zatanna 01.jpg", "Zatanna 02.jpg", "zatanna 03.jpg"])

        assert archive.get_pages() == ["Zatanna 01.jpg", "Zatanna 02.jpg", "zatanna 03.jpg"]


def test_comic_archive_sort_pages_puts_leading_underscore_page_first():
    archives = [
        ["wizard 18 c2c MS/IMG_0001.jpg", "wizard 18 c2c MS/IMG_0002.jpg", "wizard 18 c2c MS/_IMG.jpg"],
        ["Hawkworld - New Edition-009.jpg", "Hawkworld - New Edition-010.jpg", "_hawkworld book 01 - 00 ocd horus.jpg"],
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[0] == files[2]


def test_comic_archive_sort_pages_recognizes_wraparound_glued_and_underscored_covers():
    archives = [
        # Wraparound covers that name both sides are covers, not back covers
        ["The Maze Agency v1 012 - 00 ifc.jpg", "The Maze Agency v1 012 - 01.jpg", "_The Maze Agency v1 012 - 00 fc+bc.jpg"],
        ["Adventure_416_01.jpg", "Adventure_416_02.jpg", "Adventure_416_00_FC-BC.jpg"],
        # A back cover glued to its page number goes to the end
        ["Fathom 1 of 3 p00bc.jpg", "Fathom 1 of 3 p00ifc.jpg", "Fathom 1 of 3 p00_fc.jpg"],
        # Cover words joined by underscores
        ["batman_233_00a_inside_front_cover.jpg", "batman_233_66_back_cover.jpg", "batman_233_00_front_cover.jpg"],
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[0] == files[2]

        archive.get_file_list = MagicMock(return_value=["Fathom 1 of 3 p00bc.jpg", "Fathom 1 of 3 p01.jpg"])
        assert archive.get_pages()[-1] == "Fathom 1 of 3 p00bc.jpg"


def test_comic_archive_sort_pages_ignores_cover_words_in_folder_names():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        # "0f" in the folder name looks like a zero-letter cover token
        archive.get_file_list = MagicMock(return_value=[
            "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00ifc.jpg",
            "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p01.jpg",
            "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00_fc & bc.jpg",
            "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00bc.jpg",
            "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00_fc.jpg",
        ])

        pages = archive.get_pages()

        assert pages[0] == "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00_fc.jpg"
        assert pages[-1] == "Fathom [Comico] 1 0f 3/Fathom 1 of 3 p00bc.jpg"


def test_comic_archive_sort_pages_prefers_front_cover_over_wraparound():
    archives = [
        # Front cover beats the wraparound
        (["Power Factor_000- FC-BC.0.jpg", "Power Factor_001-02.jpg", "Power Factor_001-01FC.jpg"], "Power Factor_001-01FC.jpg"),
        (["NT_Showcase_01pg00FCBC.jpg", "NT_Showcase_01pg01.jpg", "NT_Showcase_01pg00FC.jpg"], "NT_Showcase_01pg00FC.jpg"),
        # The back cover's page number glued on ("fc29bc") is still a wraparound, not a back cover
        (["Robotech Macross Saga 03-01.jpg", "Robotech Macross Saga 03-00ifc.jpg", "_Robotech Macross Saga 03-00fc29bc.jpg"], "_Robotech Macross Saga 03-00fc29bc.jpg"),
        # "fc" glued to the page number is a cover
        (["scan-37card_front_Bullseye_Gird.jpg", "scan-02_Bullseye_Gird.jpg", "scan-01fc_Bullseye_Gird.jpg"], "scan-01fc_Bullseye_Gird.jpg"),
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files, cover in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[0] == cover


def test_comic_archive_get_pages_skips_macos_resource_forks():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=[
            "The Flash (2018) 054-000.jpg",
            "__MACOSX/._The Flash (2018) 054-000.jpg",
            "._The Flash (2018) 054-001.jpg",
            "The Flash (2018) 054-001.jpg",
        ])

        assert archive.get_pages() == ["The Flash (2018) 054-000.jpg", "The Flash (2018) 054-001.jpg"]


def test_comic_archive_sort_pages_compares_names_after_leading_markers():
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        archive.get_file_list = MagicMock(return_value=["__hr_Page 01.jpg", "__0xhy1coverx.jpg"])

        assert archive.get_pages()[0] == "__0xhy1coverx.jpg"


def test_comic_archive_sort_pages_ignores_lookalike_cover_tokens():
    archives = [
        # "0f" here is a typo for "of", on every page
        (["Fathom [Comico] 2 0f 3 p00ifc.jpg", "Fathom [Comico] 2 0f 3 p01.jpg", "Fathom [Comico] 2 0f 3 p00_fc.jpg"], "Fathom [Comico] 2 0f 3 p00_fc.jpg"),
        # Random hex names that happen to end in "fc"
        (["17_42fc.jpg", "1_4fda.jpg", "0_a9eb.jpg"], "0_a9eb.jpg"),
    ]
    with patch("app.services.archive.zipfile.is_zipfile", return_value=True), \
         patch("app.services.archive.zipfile.ZipFile"):
        archive = ComicArchive(Path("dummy.cbz"))

        for files, cover in archives:
            archive.get_file_list = MagicMock(return_value=files)

            assert archive.get_pages()[0] == cover
