def test_reader_page_composes_overlay_enhancement(auth_client):
    response = auth_client.get("/reader/123")

    assert response.status_code == 200
    body = response.text
    assert "window.createReader({ comicId: 123 })" in body
    assert "window.parker.applyReaderOverlayEnhancements(window.createReader({ comicId: 123 }))" in body
    assert "/static/js/reader.js" in body
    assert "/static/js/reader-overlays.js" in body
    assert "/static/js/reader-annotations.js" in body
