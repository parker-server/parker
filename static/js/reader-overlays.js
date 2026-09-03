(() => {
    const DEFAULT_OPTIONS = Object.freeze({
        debug: false,
        itemsByPage: null,
        renderers: null,
        pointerHandlers: null
    });

    function clampNormalizedValue(value) {
        if (!Number.isFinite(value)) {
            return 0;
        }

        return Math.max(0, Math.min(1, value));
    }

    function normalizePageIndex(pageIndex) {
        const value = Number.parseInt(pageIndex, 10);
        return Number.isNaN(value) ? null : value;
    }

    function getPageImage(pageShell) {
        return pageShell?.querySelector('[data-reader-page-image]') || null;
    }

    function getPageIndexFromShell(pageShell) {
        return normalizePageIndex(pageShell?.dataset?.pageIndex);
    }

    function getPageShellFromPoint(event) {
        if (typeof document.elementsFromPoint !== 'function') {
            return null;
        }

        return document.elementsFromPoint(event.clientX, event.clientY)
            .find((element) => element.matches?.('[data-reader-page-shell]')) || null;
    }

    function normalizePointFromEvent(event, pageShell) {
        const image = getPageImage(pageShell);
        if (!image) {
            return null;
        }

        const rect = image.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) {
            return null;
        }

        return {
            x: clampNormalizedValue((event.clientX - rect.left) / rect.width),
            y: clampNormalizedValue((event.clientY - rect.top) / rect.height)
        };
    }

    function buildDebugOverlay(pageIndex) {
        return [
            {
                id: `debug-border-${pageIndex}`,
                type: 'debug-border',
                page_index: pageIndex
            },
            {
                id: `debug-crosshair-${pageIndex}`,
                type: 'debug-crosshair',
                page_index: pageIndex
            },
            {
                id: `debug-label-${pageIndex}`,
                type: 'debug-label',
                page_index: pageIndex
            }
        ];
    }

    function renderDebugOverlay(item) {
        if (item.type === 'debug-border') {
            return `
                <rect
                    x="0.002"
                    y="0.002"
                    width="0.996"
                    height="0.996"
                    fill="none"
                    vector-effect="non-scaling-stroke"
                    class="reader-overlay-debug-border" />
            `;
        }

        if (item.type === 'debug-crosshair') {
            return `
                <line
                    x1="0.5"
                    y1="0"
                    x2="0.5"
                    y2="1"
                    vector-effect="non-scaling-stroke"
                    class="reader-overlay-debug-crosshair" />
                <line
                    x1="0"
                    y1="0.5"
                    x2="1"
                    y2="0.5"
                    vector-effect="non-scaling-stroke"
                    class="reader-overlay-debug-crosshair" />
            `;
        }

        if (item.type === 'debug-label') {
            return `
                <text
                    x="0.02"
                    y="0.06"
                    font-size="0.035"
                    stroke-width="0.008"
                    vector-effect="non-scaling-stroke"
                    class="reader-overlay-debug-label">Page ${item.page_index + 1}</text>
            `;
        }

        return '';
    }

    function createReaderOverlayLayer(options = {}) {
        const config = { ...DEFAULT_OPTIONS, ...options };

        return {
            enabled: true,
            debug: !!config.debug,
            interactive: false,
            activeTool: null,
            pointerPageIndex: null,
            pointerPoint: null,
            itemsByPage: config.itemsByPage || {},
            renderers: config.renderers || {},
            pointerHandlers: config.pointerHandlers || {},

            setItemsByPage(itemsByPage) {
                this.itemsByPage = itemsByPage || {};
            },

            registerRenderer(type, renderer) {
                if (!type || typeof renderer !== 'function') {
                    return;
                }

                this.renderers = { ...this.renderers, [type]: renderer };
            },

            setPointerHandlers(handlers) {
                this.pointerHandlers = handlers || {};
            },

            setDebug(value) {
                this.debug = !!value;
                if (!this.debug && !this.isInteractive()) {
                    this.handlePointerLeave();
                }
            },

            toggleDebug() {
                this.setDebug(!this.debug);
            },

            setInteractive(value) {
                this.interactive = !!value;
            },

            setActiveTool(toolName) {
                this.activeTool = toolName || null;
                this.setInteractive(!!toolName);
            },

            clearActiveTool() {
                this.setActiveTool(null);
            },

            isInteractive() {
                return this.enabled && this.interactive;
            },

            getItemsForPage(pageIndex) {
                const normalizedPageIndex = normalizePageIndex(pageIndex);
                if (normalizedPageIndex === null) {
                    return [];
                }

                const items = this.itemsByPage[String(normalizedPageIndex)] || [];

                if (!this.debug) {
                    return items;
                }

                return [...items, ...buildDebugOverlay(normalizedPageIndex)];
            },

            renderItem(item) {
                if (item.type && item.type.startsWith('debug-')) {
                    return renderDebugOverlay(item);
                }

                const renderer = this.renderers[item.type];
                if (typeof renderer === 'function') {
                    return renderer(item);
                }

                return '';
            },

            renderPage(pageIndex) {
                return this.getItemsForPage(pageIndex)
                    .map((item) => this.renderItem(item))
                    .join('');
            },

            pointFromEvent(event, pageShell) {
                return normalizePointFromEvent(event, pageShell);
            },

            updatePointer(event, pageShell, pageIndex = getPageIndexFromShell(pageShell)) {
                if (!pageShell) {
                    return null;
                }

                const point = this.pointFromEvent(event, pageShell);
                if (!point) {
                    return null;
                }

                const normalizedPageIndex = normalizePageIndex(pageIndex);
                if (normalizedPageIndex === null) {
                    return null;
                }

                this.pointerPageIndex = normalizedPageIndex;
                this.pointerPoint = point;
                return point;
            },

            handleViewportPointerMove(event) {
                if (!this.debug || this.isInteractive()) {
                    return;
                }

                const pageShell = getPageShellFromPoint(event);
                if (!pageShell) {
                    this.handlePointerLeave();
                    return;
                }

                this.updatePointer(event, pageShell);
            },

            handlePointerMove(event, pageIndex, dataContext = null) {
                if (!this.debug && !this.isInteractive()) {
                    return;
                }

                const pageShell = event.currentTarget.closest('[data-reader-page-shell]');
                const point = this.updatePointer(event, pageShell, pageIndex);

                if (!this.isInteractive() || !point) {
                    return;
                }

                event.preventDefault();
                event.stopPropagation();

                if (typeof this.pointerHandlers.move === 'function') {
                    this.pointerHandlers.move({
                        event,
                        pageIndex: normalizePageIndex(pageIndex),
                        pageShell,
                        point,
                        dataContext
                    });
                }
            },

            handlePointerLeave() {
                this.pointerPageIndex = null;
                this.pointerPoint = null;
            },

            handlePointerDown(event, pageIndex, dataContext = null) {
                if (!this.isInteractive()) {
                    return;
                }

                const pageShell = event.currentTarget.closest('[data-reader-page-shell]');
                const point = this.updatePointer(event, pageShell, pageIndex);
                if (!point) {
                    return;
                }

                event.preventDefault();
                event.stopPropagation();
                if (typeof event.currentTarget.setPointerCapture === 'function') {
                    event.currentTarget.setPointerCapture(event.pointerId);
                }

                if (typeof this.pointerHandlers.down === 'function') {
                    this.pointerHandlers.down({
                        event,
                        pageIndex: normalizePageIndex(pageIndex),
                        pageShell,
                        point,
                        dataContext
                    });
                }
            },

            handlePointerUp(event, pageIndex, dataContext = null) {
                if (!this.isInteractive()) {
                    return;
                }

                event.preventDefault();
                event.stopPropagation();
                if (typeof event.currentTarget.releasePointerCapture === 'function'
                    && event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                    event.currentTarget.releasePointerCapture(event.pointerId);
                }

                if (typeof this.pointerHandlers.up === 'function') {
                    this.pointerHandlers.up({
                        event,
                        pageIndex: normalizePageIndex(pageIndex),
                        point: this.pointerPoint,
                        dataContext
                    });
                }
            },

            getPointerLabel() {
                if (!this.pointerPoint || !Number.isInteger(this.pointerPageIndex)) {
                    return '';
                }

                const x = this.pointerPoint.x.toFixed(3);
                const y = this.pointerPoint.y.toFixed(3);
                return `Page ${this.pointerPageIndex + 1}: x ${x}, y ${y}`;
            }
        };
    }

    window.parker = window.parker || {};
    window.parker.createReaderOverlayLayer = createReaderOverlayLayer;
})();
