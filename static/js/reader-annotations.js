(() => {
    function createReaderAnnotations() {
        return {
            enabled: false,
            itemsByPage: {},

            getOverlayItemsByPage() {
                return this.itemsByPage;
            }
        };
    }

    function overlayDebugEnabledFromUrl() {
        const params = new URLSearchParams(window.location.search);
        return params.get('overlay_debug') === 'true';
    }

    function shouldPauseReaderGesture(reader) {
        return !!(reader.overlays && reader.overlays.isInteractive && reader.overlays.isInteractive());
    }

    function clickStartedOnReaderControl(event) {
        return !!event.target.closest(
            '.reader-toolbar, .reader-controls, .scrubber-wrapper, .settings-panel, ' +
            '.bookmark-modal, .goto-modal, button, input, select, textarea, a, [role="button"]'
        );
    }

    function getFitWidthNavigationZone(event) {
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        if (viewportWidth <= 0) {
            return null;
        }

        const xRatio = event.clientX / viewportWidth;
        if (xRatio <= 0.2) {
            return 'left';
        }

        if (xRatio >= 0.8) {
            return 'right';
        }

        return 'center';
    }

    function wrapReaderGesture(reader, methodName) {
        const originalMethod = reader[methodName];
        if (typeof originalMethod !== 'function') {
            return;
        }

        reader[methodName] = function wrappedReaderGesture(...args) {
            if (shouldPauseReaderGesture(this)) {
                return;
            }

            return originalMethod.apply(this, args);
        };
    }

    function applyReaderOverlayEnhancements(reader) {
        const annotations = createReaderAnnotations();
        const overlays = window.parker.createReaderOverlayLayer({
            debug: overlayDebugEnabledFromUrl(),
            itemsByPage: annotations.getOverlayItemsByPage()
        });

        reader.annotations = annotations;
        reader.overlays = overlays;
        reader.fitWidthPointerZone = null;

        reader.toggleOverlayDebug = function toggleOverlayDebug() {
            this.overlays.toggleDebug();
            window.parker.showToast(this.overlays.debug ? 'Overlay debug on' : 'Overlay debug off');
        };

        reader.toggleAnnotationMode = function toggleAnnotationMode() {
            this.annotations.enabled = !this.annotations.enabled;
            this.overlays.setActiveTool(this.annotations.enabled ? 'debug-select' : null);
            window.parker.showToast(this.annotations.enabled ? 'Annotation mode placeholder on' : 'Annotation mode placeholder off');
        };

        reader.handleFitWidthPointerMove = function handleFitWidthPointerMove(event) {
            if (this.readingMode !== 'paged' || this.fitMode !== 'width') {
                this.fitWidthPointerZone = null;
                return;
            }

            if (event.defaultPrevented || shouldPauseReaderGesture(this) || clickStartedOnReaderControl(event)) {
                this.fitWidthPointerZone = null;
                return;
            }

            const zone = getFitWidthNavigationZone(event);
            this.fitWidthPointerZone = zone === 'left' || zone === 'right' ? 'edge' : null;
        };

        reader.clearFitWidthPointerZone = function clearFitWidthPointerZone() {
            this.fitWidthPointerZone = null;
        };

        reader.handleFitWidthContentClick = function handleFitWidthContentClick(event) {
            if (this.readingMode !== 'paged' || this.fitMode !== 'width') {
                return;
            }

            if (event.defaultPrevented || shouldPauseReaderGesture(this) || clickStartedOnReaderControl(event)) {
                return;
            }

            const zone = getFitWidthNavigationZone(event);
            if (!zone) {
                return;
            }

            this.handleZoneClick(zone);
        };

        wrapReaderGesture(reader, 'handleTouchStart');
        wrapReaderGesture(reader, 'handleTouchEnd');
        wrapReaderGesture(reader, 'handleZoneClick');

        return reader;
    }

    function createReaderWithOverlays(options) {
        if (typeof window.createReader !== 'function') {
            throw new Error('Parker reader has not been loaded yet.');
        }

        return applyReaderOverlayEnhancements(window.createReader(options));
    }

    window.parker = window.parker || {};
    window.parker.createReaderAnnotations = createReaderAnnotations;
    window.parker.applyReaderOverlayEnhancements = applyReaderOverlayEnhancements;
    window.createReaderWithOverlays = createReaderWithOverlays;
})();
