import pytest


@pytest.mark.browser
def test_metadata_logo_helper_uses_filesystem_safe_fallback(page, browser_server):
    page.goto(f"{browser_server['base_url']}/", wait_until="networkidle")

    result = page.evaluate(
        """async () => {
            const urls = window.parker.metadataLogoCandidates('publishers', 'Valiant/Acclaim');
            const fallbackUrl = urls.find((url) => url.endsWith('/ValiantAcclaim.png'));
            const fallbackStatus = fallbackUrl ? (await fetch(fallbackUrl)).status : null;
            const spacedFallbackPaths = window.parker
                .metadataLogoCandidates('publishers', 'Awesome/Comics')
                .map((url) => new URL(url, window.location.origin).pathname);
            const loadedFallbackPath = await new Promise((resolve, reject) => {
                const badge = document.createElement('span');
                const image = document.createElement('img');
                const timeout = window.setTimeout(() => {
                    badge.remove();
                    reject(new Error('Timed out waiting for metadata logo fallback'));
                }, 3000);

                image.addEventListener('error', () => {
                    window.parker.metadataLogoFallback(image, 'publishers', 'Valiant/Acclaim');
                });
                image.addEventListener('load', () => {
                    if (!image.src.endsWith('/ValiantAcclaim.png')) return;

                    const path = new URL(image.src, window.location.origin).pathname;
                    window.clearTimeout(timeout);
                    badge.remove();
                    resolve(path);
                });

                badge.appendChild(image);
                document.body.appendChild(badge);
                image.src = window.parker.metadataLogoUrl('publishers', 'Valiant/Acclaim');
            });

            return {
                paths: urls.map((url) => new URL(url, window.location.origin).pathname),
                fallbackStatus,
                spacedFallbackPaths,
                loadedFallbackPath,
            };
        }"""
    )

    assert result["paths"][0] == "/static/img/publishers/Valiant%2FAcclaim.png"
    assert "/static/img/publishers/ValiantAcclaim.png" in result["paths"]
    assert result["spacedFallbackPaths"].index("/static/img/publishers/Awesome%20Comics.png") < result[
        "spacedFallbackPaths"
    ].index("/static/img/publishers/AwesomeComics.png")
    assert result["fallbackStatus"] == 200
    assert result["loadedFallbackPath"] == "/static/img/publishers/ValiantAcclaim.png"
