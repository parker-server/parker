(() => {
    const DEFAULT_ANNOTATION_COLOR = '#facc15';
    const ANNOTATION_LAYER_VISIBILITY_KEY = 'reader_annotationLayerVisible';
    const ANNOTATION_COLORS = Object.freeze(['#facc15', '#38bdf8', '#fb7185', '#34d399']);
    const RECTANGLE_MIN_SIZE = 0.01;
    const ANNOTATION_DRAG_THRESHOLD = 0.004;
    const PIN_HALF_WIDTH = 0.032;
    const PIN_HEIGHT = 0.094;
    const ANNOTATION_PEEK_MAX_BODY_LENGTH = 160;
    const ANNOTATION_PEEK_WIDTH = 288;
    const ANNOTATION_PEEK_HEIGHT = 112;

    function clampNormalizedValue(value) {
        if (!Number.isFinite(value)) {
            return 0;
        }

        return Math.max(0, Math.min(1, value));
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function escapeAttribute(value) {
        return escapeHtml(value);
    }

    function normalizeText(value) {
        const trimmed = String(value || '').trim();
        return trimmed || null;
    }

    function safeColor(value) {
        const color = String(value || DEFAULT_ANNOTATION_COLOR).trim();
        return /^#[0-9a-f]{6}$/i.test(color) ? color.toLowerCase() : DEFAULT_ANNOTATION_COLOR;
    }

    function loadAnnotationLayerVisibilityPreference() {
        return window.parker.storage.get(ANNOTATION_LAYER_VISIBILITY_KEY, true) !== false;
    }

    function persistAnnotationLayerVisibilityPreference(isVisible) {
        window.parker.storage.set(ANNOTATION_LAYER_VISIBILITY_KEY, !!isVisible);
    }

    function normalizeAnchorPoint(point) {
        return {
            x: clampNormalizedValue(point?.x ?? 0.5),
            y: clampNormalizedValue(point?.y ?? 0.5)
        };
    }

    function normalizeRectangle(start, end) {
        const startPoint = normalizeAnchorPoint(start);
        const endPoint = normalizeAnchorPoint(end);
        const x = Math.min(startPoint.x, endPoint.x);
        const y = Math.min(startPoint.y, endPoint.y);

        return {
            x,
            y,
            width: Math.abs(endPoint.x - startPoint.x),
            height: Math.abs(endPoint.y - startPoint.y)
        };
    }

    function hasUsableRectangleSize(anchor) {
        return anchor.width >= RECTANGLE_MIN_SIZE && anchor.height >= RECTANGLE_MIN_SIZE;
    }

    function sortAnnotations(annotations) {
        return [...annotations].sort((left, right) => {
            if (left.page_index !== right.page_index) {
                return left.page_index - right.page_index;
            }

            return left.id - right.id;
        });
    }

    function getAnnotationTitle(annotation) {
        if (!annotation) {
            return '';
        }

        if (annotation.title) {
            return annotation.title;
        }

        if (annotation.body) {
            return annotation.body;
        }

        return annotation.kind === 'rectangle' ? 'Rectangle annotation' : 'Page pin';
    }

    function getAnnotationBodyPreview(annotation) {
        const body = String(annotation?.body || '').trim();
        if (!body || body === getAnnotationTitle(annotation)) {
            return '';
        }

        if (body.length <= ANNOTATION_PEEK_MAX_BODY_LENGTH) {
            return body;
        }

        return `${body.slice(0, ANNOTATION_PEEK_MAX_BODY_LENGTH - 3)}...`;
    }

    function createOverlayItem(annotation, selectedId, draggingId) {
        return {
            id: `annotation-${annotation.id}`,
            type: 'annotation',
            annotation_id: annotation.id,
            page_index: annotation.page_index,
            kind: annotation.kind,
            title: annotation.title,
            body: annotation.body,
            color: annotation.color,
            anchor: annotation.anchor || {},
            selected: annotation.id === selectedId,
            dragging: annotation.id === draggingId,
            draft: false
        };
    }

    function createDraftOverlayItem(draft) {
        return {
            id: 'annotation-draft',
            type: 'annotation',
            annotation_id: null,
            page_index: draft.page_index,
            kind: draft.kind,
            title: 'Draft annotation',
            body: null,
            color: draft.color,
            anchor: draft.anchor,
            selected: false,
            draft: true
        };
    }

    function buildOverlayItemsByPage(annotations, selectedId, draft, draggingId) {
        const itemsByPage = {};

        for (const annotation of annotations) {
            const key = String(annotation.page_index);
            itemsByPage[key] = itemsByPage[key] || [];
            itemsByPage[key].push(createOverlayItem(annotation, selectedId, draggingId));
        }

        if (draft) {
            const key = String(draft.page_index);
            itemsByPage[key] = itemsByPage[key] || [];
            itemsByPage[key].push(createDraftOverlayItem(draft));
        }

        return itemsByPage;
    }

    function getAnnotationIdFromEvent(event) {
        const annotationElement = event.target.closest?.('[data-reader-annotation-id]');
        const rawId = annotationElement?.dataset?.readerAnnotationId;
        const annotationId = Number.parseInt(rawId, 10);
        return Number.isNaN(annotationId) ? null : annotationId;
    }

    function cloneAnchor(anchor) {
        return { ...(anchor || {}) };
    }

    function getAnnotationById(reader, annotationId) {
        return reader.annotations.items.find((annotation) => annotation.id === annotationId) || null;
    }

    function getAnnotationFromEvent(reader, event) {
        const annotationId = getAnnotationIdFromEvent(event);
        return annotationId ? getAnnotationById(reader, annotationId) : null;
    }

    function getAnnotationIdFromElement(element) {
        const annotationElement = element?.closest?.('[data-reader-annotation-id]');
        const rawId = annotationElement?.dataset?.readerAnnotationId;
        const annotationId = Number.parseInt(rawId, 10);
        return Number.isNaN(annotationId) ? null : annotationId;
    }

    function getAnnotationAnchorOrigin(annotation) {
        const anchor = annotation.anchor || {};

        return {
            x: clampNormalizedValue(Number(anchor.x)),
            y: clampNormalizedValue(Number(anchor.y))
        };
    }

    function clampRectangleAnchor(anchor) {
        const width = clampNormalizedValue(Number(anchor.width));
        const height = clampNormalizedValue(Number(anchor.height));

        return {
            x: Math.max(0, Math.min(1 - width, Number(anchor.x) || 0)),
            y: Math.max(0, Math.min(1 - height, Number(anchor.y) || 0)),
            width,
            height
        };
    }

    function moveAnnotationAnchor(annotation, point, offset) {
        const normalizedPoint = normalizeAnchorPoint(point);
        const nextOrigin = {
            x: normalizedPoint.x - offset.x,
            y: normalizedPoint.y - offset.y
        };

        if (annotation.kind === 'rectangle') {
            const anchor = annotation.anchor || {};
            return clampRectangleAnchor({
                ...anchor,
                x: nextOrigin.x,
                y: nextOrigin.y
            });
        }

        return normalizeAnchorPoint(nextOrigin);
    }

    function updateAnnotationAnchor(reader, annotationId, anchor) {
        const nextAnnotations = reader.annotations.items.map((annotation) => {
            if (annotation.id !== annotationId) {
                return annotation;
            }

            return {
                ...annotation,
                anchor: cloneAnchor(anchor)
            };
        });

        reader.annotations.items = sortAnnotations(nextAnnotations);
        syncAnnotationOverlays(reader);
    }

    function hasDragMoved(drag, point) {
        const normalizedPoint = normalizeAnchorPoint(point);
        const distance = Math.hypot(
            normalizedPoint.x - drag.startPoint.x,
            normalizedPoint.y - drag.startPoint.y
        );

        return distance >= ANNOTATION_DRAG_THRESHOLD;
    }

    function buildMapPinPath(anchor) {
        const tipX = anchor.x;
        const tipY = anchor.y;
        const shoulderY = Math.max(0, tipY - PIN_HEIGHT * 0.36);
        const centerY = Math.max(0, tipY - PIN_HEIGHT * 0.64);
        const topY = Math.max(0, tipY - PIN_HEIGHT);
        const leftX = Math.max(0, tipX - PIN_HALF_WIDTH);
        const rightX = Math.min(1, tipX + PIN_HALF_WIDTH);
        const innerLeftX = Math.max(0, tipX - PIN_HALF_WIDTH * 0.82);
        const innerRightX = Math.min(1, tipX + PIN_HALF_WIDTH * 0.82);

        return [
            `M ${tipX} ${tipY}`,
            `C ${innerLeftX} ${shoulderY} ${leftX} ${centerY} ${leftX} ${topY + PIN_HEIGHT * 0.38}`,
            `C ${leftX} ${topY + PIN_HEIGHT * 0.16} ${innerLeftX} ${topY} ${tipX} ${topY}`,
            `C ${innerRightX} ${topY} ${rightX} ${topY + PIN_HEIGHT * 0.16} ${rightX} ${topY + PIN_HEIGHT * 0.38}`,
            `C ${rightX} ${centerY} ${innerRightX} ${shoulderY} ${tipX} ${tipY}`,
            'Z'
        ].join(' ');
    }

    function renderPin(item, color, label, classes, dataAttributes) {
        const anchor = normalizeAnchorPoint(item.anchor);
        const labelText = escapeHtml(label);
        const markerPath = buildMapPinPath(anchor);
        const targetY = clampNormalizedValue(anchor.y - PIN_HEIGHT * 0.48);

        return `
            <g class="${classes}" ${dataAttributes}>
                <title>${labelText}</title>
                <path
                    d="${markerPath}"
                    fill="${color}"
                    stroke="rgba(0, 0, 0, 0.78)"
                    vector-effect="non-scaling-stroke"
                    class="reader-annotation-pin-marker" />
                <circle
                    cx="${anchor.x}"
                    cy="${targetY}"
                    r="0.04"
                    fill="#ffffff"
                    fill-opacity="0.001"
                    class="reader-annotation-hit-target" />
            </g>
        `;
    }

    function renderRectangle(item, color, label, classes, dataAttributes) {
        const anchor = item.anchor || {};
        const x = clampNormalizedValue(Number(anchor.x));
        const y = clampNormalizedValue(Number(anchor.y));
        const width = clampNormalizedValue(Number(anchor.width));
        const height = clampNormalizedValue(Number(anchor.height));
        const labelText = escapeHtml(label);

        return `
            <g class="${classes}" ${dataAttributes}>
                <title>${labelText}</title>
                <rect
                    x="${x}"
                    y="${y}"
                    width="${width}"
                    height="${height}"
                    fill="${color}"
                    class="reader-annotation-rect-fill" />
                <rect
                    x="${x}"
                    y="${y}"
                    width="${width}"
                    height="${height}"
                    fill="none"
                    stroke="${color}"
                    vector-effect="non-scaling-stroke"
                    class="reader-annotation-rect-stroke" />
                <rect
                    x="${x}"
                    y="${y}"
                    width="${width}"
                    height="${height}"
                    fill="#ffffff"
                    fill-opacity="0.001"
                    class="reader-annotation-hit-target" />
            </g>
        `;
    }

    function renderAnnotationOverlayItem(item) {
        const color = safeColor(item.color);
        const label = getAnnotationTitle(item);
        const classNames = [
            'reader-annotation-overlay-item',
            `reader-annotation-${item.kind}`,
            item.selected ? 'is-selected' : '',
            item.dragging ? 'is-dragging' : '',
            item.draft ? 'is-draft' : ''
        ].filter(Boolean).join(' ');
        const dataAttributes = item.draft
            ? 'data-reader-annotation-draft="true"'
            : `data-reader-annotation-id="${escapeAttribute(item.annotation_id)}"`;

        if (item.kind === 'rectangle') {
            return renderRectangle(item, color, label, classNames, dataAttributes);
        }

        return renderPin(item, color, label, classNames, dataAttributes);
    }

    async function readJsonResponse(response, fallbackMessage) {
        let payload = {};

        try {
            payload = await response.json();
        } catch {
            payload = {};
        }

        if (!response.ok) {
            throw new Error(payload.detail || fallbackMessage);
        }

        return payload;
    }

    function createReaderAnnotations() {
        return {
            enabled: false,
            isPanelOpen: false,
            isLayerVisible: true,
            isRepositioning: false,
            isBusy: false,
            loaded: false,
            items: [],
            itemsByPage: {},
            peek: {
                annotationId: null,
                mode: null,
                x: 0,
                y: 0
            },
            selectedId: null,
            activeTool: 'pin',
            draft: null,
            draftStart: null,
            drag: null,
            form: {
                title: '',
                body: '',
                color: DEFAULT_ANNOTATION_COLOR
            },
            editForm: {
                title: '',
                body: '',
                color: DEFAULT_ANNOTATION_COLOR
            },
            colorChoices: [...ANNOTATION_COLORS],

            get selectedAnnotation() {
                return this.items.find((annotation) => annotation.id === this.selectedId) || null;
            },

            get peekAnnotation() {
                return this.items.find((annotation) => annotation.id === this.peek.annotationId) || null;
            },

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
        return !!event.target.closest?.(
            '.reader-toolbar, .reader-controls, .scrubber-wrapper, .settings-panel, ' +
            '.reader-annotation-panel, .reader-annotation-peek, .bookmark-modal, .goto-modal, ' +
            'button, input, select, textarea, a, [role="button"]'
        );
    }

    function keyStartedInAnnotationPanel(event) {
        return !!event.target.closest?.('.reader-annotation-panel');
    }

    function handleAnnotationEscape(reader, event) {
        if (event.key !== 'Escape') {
            return false;
        }

        if (!reader.annotations.isPanelOpen && !reader.annotations.enabled && !reader.annotations.peek.annotationId) {
            return false;
        }

        event.preventDefault();
        if (reader.annotations.peek.annotationId && !reader.annotations.isPanelOpen && !reader.annotations.enabled) {
            reader.hideAnnotationPeek();
            return true;
        }

        if (reader.annotations.isRepositioning) {
            reader.finishAnnotationReposition();
            return true;
        }

        if (reader.annotations.selectedId) {
            reader.clearAnnotationSelection();
            return true;
        }

        reader.closeAnnotations();
        return true;
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

    function shouldRenderAnnotationLayer(reader) {
        return !!(
            reader.annotations.isLayerVisible
            || reader.annotations.enabled
            || reader.annotations.isPanelOpen
        );
    }

    function canShowAnnotationPeek(reader) {
        return !!(
            shouldRenderAnnotationLayer(reader)
            && !reader.annotations.enabled
            && !reader.annotations.isPanelOpen
            && !reader.annotations.isRepositioning
        );
    }

    function pointerUsesHover(event) {
        return !event.pointerType || event.pointerType === 'mouse';
    }

    function pointerUsesTouchPeek(event) {
        return event.pointerType === 'touch'
            || event.pointerType === 'pen'
            || window.matchMedia?.('(hover: none)')?.matches;
    }

    function getPeekPosition(event) {
        return {
            x: Number.isFinite(event.clientX) ? event.clientX : window.innerWidth / 2,
            y: Number.isFinite(event.clientY) ? event.clientY : window.innerHeight / 2
        };
    }

    function getPageShellFromViewportPoint(event) {
        if (
            typeof document.elementsFromPoint !== 'function'
            || !Number.isFinite(event.clientX)
            || !Number.isFinite(event.clientY)
        ) {
            return null;
        }

        return document.elementsFromPoint(event.clientX, event.clientY)
            .find((element) => element.matches?.('[data-reader-page-shell]')) || null;
    }

    function getPagePointFromViewportEvent(event, pageShell) {
        const image = pageShell?.querySelector('[data-reader-page-image]');
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

    function getPageIndexFromShell(pageShell) {
        const pageIndex = Number.parseInt(pageShell?.dataset?.pageIndex, 10);
        return Number.isNaN(pageIndex) ? null : pageIndex;
    }

    function annotationContainsPoint(annotation, point) {
        if (!annotation || !point) {
            return false;
        }

        if (annotation.kind === 'rectangle') {
            const anchor = clampRectangleAnchor(annotation.anchor || {});
            return point.x >= anchor.x
                && point.x <= anchor.x + anchor.width
                && point.y >= anchor.y
                && point.y <= anchor.y + anchor.height;
        }

        const anchor = normalizeAnchorPoint(annotation.anchor);
        const targetY = clampNormalizedValue(anchor.y - PIN_HEIGHT * 0.48);
        return Math.hypot(point.x - anchor.x, point.y - targetY) <= 0.045;
    }

    function getAnnotationAtViewportPoint(reader, event) {
        if (!canShowAnnotationPeek(reader)) {
            return null;
        }

        const pageShell = getPageShellFromViewportPoint(event);
        const pageIndex = getPageIndexFromShell(pageShell);
        const point = getPagePointFromViewportEvent(event, pageShell);
        if (pageIndex === null || !point) {
            return null;
        }

        return reader.annotations.items
            .filter((annotation) => annotation.page_index === pageIndex)
            .reverse()
            .find((annotation) => annotationContainsPoint(annotation, point)) || null;
    }

    function syncAnnotationOverlays(reader) {
        const layerIsVisible = shouldRenderAnnotationLayer(reader);

        reader.annotations.itemsByPage = buildOverlayItemsByPage(
            layerIsVisible ? reader.annotations.items : [],
            layerIsVisible ? reader.annotations.selectedId : null,
            layerIsVisible ? reader.annotations.draft : null,
            layerIsVisible ? reader.annotations.drag?.annotationId || null : null
        );

        reader.overlays.setItemsByPage(reader.annotations.getOverlayItemsByPage());
    }

    function startAnnotationMove(reader, annotationId, pageIndex, point) {
        const annotation = getAnnotationById(reader, annotationId);
        if (!annotation) {
            return false;
        }

        reader.selectAnnotation(annotationId);

        if (reader.isIncognito || reader.annotations.isBusy || annotation.page_index !== pageIndex) {
            return true;
        }

        const anchorOrigin = getAnnotationAnchorOrigin(annotation);
        const startPoint = normalizeAnchorPoint(point);
        reader.annotations.drag = {
            annotationId,
            pageIndex,
            startPoint,
            offset: {
                x: startPoint.x - anchorOrigin.x,
                y: startPoint.y - anchorOrigin.y
            },
            originalAnchor: cloneAnchor(annotation.anchor),
            moved: false
        };
        syncAnnotationOverlays(reader);
        return true;
    }

    function updateAnnotationMove(reader, pageIndex, point) {
        const drag = reader.annotations.drag;
        if (!drag || drag.pageIndex !== pageIndex) {
            return false;
        }

        if (!drag.moved && !hasDragMoved(drag, point)) {
            return true;
        }

        const annotation = getAnnotationById(reader, drag.annotationId);
        if (!annotation) {
            reader.annotations.drag = null;
            syncAnnotationOverlays(reader);
            return false;
        }

        drag.moved = true;
        updateAnnotationAnchor(reader, drag.annotationId, moveAnnotationAnchor(annotation, point, drag.offset));
        return true;
    }

    function finishAnnotationMove(reader) {
        const drag = reader.annotations.drag;
        if (!drag) {
            return false;
        }

        reader.annotations.drag = null;
        syncAnnotationOverlays(reader);

        if (!drag.moved) {
            return true;
        }

        const annotation = getAnnotationById(reader, drag.annotationId);
        if (!annotation) {
            return true;
        }

        void reader.saveAnnotationPosition(annotation.id, annotation.anchor, drag.originalAnchor);
        return true;
    }

    function startAnnotationDraft(reader, pageIndex, point) {
        reader.annotations.draftStart = {
            page_index: pageIndex,
            point: normalizeAnchorPoint(point)
        };
        reader.annotations.draft = {
            page_index: pageIndex,
            kind: 'rectangle',
            color: reader.annotations.form.color,
            anchor: normalizeRectangle(point, point)
        };
        syncAnnotationOverlays(reader);
    }

    function updateAnnotationDraft(reader, pageIndex, point) {
        const draftStart = reader.annotations.draftStart;
        if (!draftStart || draftStart.page_index !== pageIndex) {
            return;
        }

        reader.annotations.draft = {
            page_index: pageIndex,
            kind: 'rectangle',
            color: reader.annotations.form.color,
            anchor: normalizeRectangle(draftStart.point, point)
        };
        syncAnnotationOverlays(reader);
    }

    function clearAnnotationDraft(reader) {
        reader.annotations.draft = null;
        reader.annotations.draftStart = null;
        syncAnnotationOverlays(reader);
    }

    function handleAnnotationPointerDown(reader, context) {
        const annotationId = getAnnotationIdFromEvent(context.event);
        if (annotationId && startAnnotationMove(reader, annotationId, context.pageIndex, context.point)) {
            return;
        }

        if (reader.annotations.isRepositioning) {
            return;
        }

        if (reader.isIncognito) {
            window.parker.showToast('Incognito Mode: Annotation changes are disabled.');
            return;
        }

        if (reader.annotations.activeTool === 'rectangle') {
            startAnnotationDraft(reader, context.pageIndex, context.point);
            return;
        }

        void reader.createAnnotationFromOverlay('pin', context.pageIndex, normalizeAnchorPoint(context.point));
    }

    function handleAnnotationPointerMove(reader, context) {
        if (updateAnnotationMove(reader, context.pageIndex, context.point)) {
            return;
        }

        if (reader.annotations.activeTool !== 'rectangle' || !reader.annotations.draftStart) {
            return;
        }

        updateAnnotationDraft(reader, context.pageIndex, context.point);
    }

    function handleAnnotationPointerUp(reader) {
        if (finishAnnotationMove(reader)) {
            return;
        }

        const draft = reader.annotations.draft;
        if (!draft) {
            return;
        }

        const anchor = draft.anchor;
        clearAnnotationDraft(reader);

        if (!hasUsableRectangleSize(anchor) || reader.isIncognito) {
            return;
        }

        void reader.createAnnotationFromOverlay('rectangle', draft.page_index, anchor);
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

    function wrapReaderMethod(reader, methodName, wrapper) {
        const originalMethod = reader[methodName];
        if (typeof originalMethod !== 'function') {
            return;
        }

        reader[methodName] = wrapper(originalMethod);
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
        reader.annotationPeekHideTimer = null;
        reader.annotationPeekSuppressNextClick = false;

        overlays.registerRenderer('annotation', renderAnnotationOverlayItem);
        overlays.setPointerHandlers({
            down: (context) => handleAnnotationPointerDown(context.dataContext || reader, context),
            move: (context) => handleAnnotationPointerMove(context.dataContext || reader, context),
            up: (context) => handleAnnotationPointerUp(context.dataContext || reader)
        });

        reader.cancelAnnotationPeekHide = function cancelAnnotationPeekHide() {
            if (this.annotationPeekHideTimer) {
                window.clearTimeout(this.annotationPeekHideTimer);
                this.annotationPeekHideTimer = null;
            }
        };

        reader.hideAnnotationPeek = function hideAnnotationPeek() {
            this.cancelAnnotationPeekHide();
            this.annotations.peek = {
                annotationId: null,
                mode: null,
                x: 0,
                y: 0
            };
        };

        reader.queueAnnotationPeekHide = function queueAnnotationPeekHide() {
            if (this.annotations.peek.mode !== 'hover') {
                return;
            }

            this.hideAnnotationPeek();
        };

        reader.showAnnotationPeek = function showAnnotationPeek(annotationOrId, event, mode = 'hover') {
            if (!canShowAnnotationPeek(this)) {
                return;
            }

            const annotation = typeof annotationOrId === 'object'
                ? annotationOrId
                : getAnnotationById(this, annotationOrId);
            if (!annotation) {
                return;
            }

            const position = getPeekPosition(event);
            this.cancelAnnotationPeekHide();
            this.annotations.peek = {
                annotationId: annotation.id,
                mode,
                x: position.x,
                y: position.y
            };
        };

        reader.handleAnnotationPeekPointerOver = function handleAnnotationPeekPointerOver(event) {
            if (!canShowAnnotationPeek(this) || !pointerUsesHover(event)) {
                return;
            }

            const annotation = getAnnotationFromEvent(this, event);
            if (annotation) {
                this.showAnnotationPeek(annotation, event, 'hover');
            }
        };

        reader.handleAnnotationPeekPointerMove = function handleAnnotationPeekPointerMove(event) {
            if (!canShowAnnotationPeek(this) || !pointerUsesHover(event)) {
                return;
            }

            const annotation = getAnnotationFromEvent(this, event);
            if (annotation) {
                this.showAnnotationPeek(annotation, event, 'hover');
            }
        };

        reader.handleAnnotationPeekPointerOut = function handleAnnotationPeekPointerOut(event) {
            const annotationId = getAnnotationIdFromEvent(event);
            if (!annotationId || this.annotations.peek.annotationId !== annotationId) {
                return;
            }

            if (getAnnotationIdFromElement(event.relatedTarget) === annotationId) {
                return;
            }

            this.queueAnnotationPeekHide();
        };

        reader.handleAnnotationPeekPointerDown = function handleAnnotationPeekPointerDown(event) {
            if (!canShowAnnotationPeek(this) || !pointerUsesTouchPeek(event)) {
                return;
            }

            const annotation = getAnnotationFromEvent(this, event);
            if (!annotation) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();
            this.annotationPeekSuppressNextClick = true;
            this.showAnnotationPeek(annotation, event, 'touch');
        };

        reader.handleAnnotationPeekClick = function handleAnnotationPeekClick(event) {
            if (!getAnnotationIdFromEvent(event)) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();
        };

        reader.handleAnnotationOutsidePointerDown = function handleAnnotationOutsidePointerDown(event) {
            const startedOnReaderControl = clickStartedOnReaderControl(event);

            if (!startedOnReaderControl && canShowAnnotationPeek(this) && pointerUsesTouchPeek(event)) {
                const annotation = getAnnotationAtViewportPoint(this, event);
                if (annotation) {
                    event.preventDefault();
                    event.stopPropagation();
                    this.annotationPeekSuppressNextClick = true;
                    this.showAnnotationPeek(annotation, event, 'touch');
                    return;
                }
            }

            if (!this.annotations.peek.annotationId) {
                return;
            }

            if (startedOnReaderControl || event.target.closest?.('[data-reader-annotation-id]')) {
                return;
            }

            this.hideAnnotationPeek();
        };

        reader.handleAnnotationPeekViewportPointerMove = function handleAnnotationPeekViewportPointerMove(event) {
            if (clickStartedOnReaderControl(event)) {
                return;
            }

            if (!canShowAnnotationPeek(this) || !pointerUsesHover(event)) {
                return;
            }

            const annotation = getAnnotationAtViewportPoint(this, event);
            if (annotation) {
                this.showAnnotationPeek(annotation, event, 'hover');
                return;
            }

            if (this.annotations.peek.mode === 'hover') {
                this.queueAnnotationPeekHide();
            }
        };

        reader.handleAnnotationPeekViewportClick = function handleAnnotationPeekViewportClick(event) {
            if (this.annotationPeekSuppressNextClick) {
                this.annotationPeekSuppressNextClick = false;
                event.preventDefault();
                event.stopPropagation();
                return;
            }

            if (clickStartedOnReaderControl(event)) {
                return;
            }

            const annotation = getAnnotationAtViewportPoint(this, event);
            if (!annotation) {
                return;
            }

            event.preventDefault();
            event.stopPropagation();
            this.showAnnotationPeek(annotation, event, pointerUsesTouchPeek(event) ? 'touch' : 'hover');
        };

        reader.getAnnotationPeekStyle = function getAnnotationPeekStyle() {
            const peek = this.annotations.peek;
            if (!peek.annotationId || peek.mode === 'touch') {
                return '';
            }

            const margin = 12;
            const gap = 14;
            const width = Math.max(180, Math.min(ANNOTATION_PEEK_WIDTH, window.innerWidth - margin * 2));
            const height = ANNOTATION_PEEK_HEIGHT;
            let left = peek.x + gap;
            let top = peek.y + gap;

            if (left + width > window.innerWidth - margin) {
                left = peek.x - width - gap;
            }

            if (top + height > window.innerHeight - margin) {
                top = peek.y - height - gap;
            }

            left = Math.max(margin, Math.min(left, window.innerWidth - width - margin));
            top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));

            return `left: ${left}px; top: ${top}px; width: ${width}px;`;
        };

        reader.getAnnotationPeekBody = function getAnnotationPeekBody(annotation) {
            return getAnnotationBodyPreview(annotation);
        };

        reader.openAnnotationFromPeek = async function openAnnotationFromPeek() {
            const annotationId = this.annotations.peek.annotationId;
            if (!annotationId) {
                return;
            }

            this.hideAnnotationPeek();
            await this.openAnnotations();
            this.selectAnnotation(annotationId);
        };

        reader.initializeAnnotationLayerVisibility = function initializeAnnotationLayerVisibility() {
            this.annotations.isLayerVisible = this.isIncognito
                ? true
                : loadAnnotationLayerVisibilityPreference();
            syncAnnotationOverlays(this);
        };

        reader.setAnnotationLayerVisibility = function setAnnotationLayerVisibility(isVisible, options = {}) {
            const shouldPersist = options.persist !== false;
            const shouldNotify = options.notify !== false;

            this.annotations.isLayerVisible = !!isVisible;

            if (shouldPersist && !this.isIncognito) {
                persistAnnotationLayerVisibilityPreference(this.annotations.isLayerVisible);
            }

            syncAnnotationOverlays(this);

            if (!this.annotations.isLayerVisible) {
                this.hideAnnotationPeek();
            }

            if (shouldNotify) {
                window.parker.showToast(
                    this.annotations.isLayerVisible
                        ? 'Annotations shown while reading'
                        : 'Annotations hidden while reading'
                );
            }
        };

        reader.toggleAnnotationLayerVisibility = function toggleAnnotationLayerVisibility() {
            this.setAnnotationLayerVisibility(!this.annotations.isLayerVisible);
        };

        reader.getAnnotationLayerVisibilityLabel = function getAnnotationLayerVisibilityLabel() {
            return this.annotations.isLayerVisible
                ? 'Hide annotations while reading'
                : 'Show annotations while reading';
        };

        reader.loadAnnotations = async function loadAnnotations() {
            try {
                const response = await fetch(window.parker.route('annotations.comic_annotations', { comic_id: this.comicId }));
                const payload = await readJsonResponse(response, 'Failed to load annotations');

                this.annotations.items = sortAnnotations(payload);
                this.annotations.loaded = true;
                syncAnnotationOverlays(this);
            } catch (error) {
                console.error(error);
            }
        };

        reader.openAnnotations = async function openAnnotations() {
            this.hideAnnotationPeek();
            this.showGoto = false;
            this.showSettings = false;
            this.showBookmarks = false;
            if (typeof this.cancelBookmarkEdit === 'function') {
                this.cancelBookmarkEdit();
            }

            this.annotations.isPanelOpen = true;
            this.annotations.enabled = true;
            this.annotations.isRepositioning = false;
            this.setAnnotationTool(this.annotations.activeTool || 'pin');

            if (!this.annotations.loaded) {
                await this.loadAnnotations();
            }

            this.$nextTick(() => {
                if (this.$refs.annotationTitleInput) {
                    this.$refs.annotationTitleInput.focus();
                }
            });
        };

        reader.closeAnnotations = function closeAnnotations() {
            this.annotations.isPanelOpen = false;
            this.annotations.enabled = false;
            this.annotations.isRepositioning = false;
            this.annotations.selectedId = null;
            this.annotations.drag = null;
            clearAnnotationDraft(this);
            this.overlays.clearActiveTool();
            this.resetControlFocus();
        };

        reader.toggleAnnotationMode = function toggleAnnotationMode() {
            if (this.annotations.isPanelOpen) {
                this.closeAnnotations();
                return;
            }

            void this.openAnnotations();
        };

        reader.setAnnotationTool = function setAnnotationTool(toolName) {
            const nextTool = toolName === 'rectangle' ? 'rectangle' : 'pin';
            this.annotations.activeTool = nextTool;
            this.annotations.enabled = true;
            this.annotations.isRepositioning = false;
            this.overlays.setActiveTool(nextTool);
            syncAnnotationOverlays(this);
        };

        reader.startAnnotationReposition = function startAnnotationReposition(annotationId = this.annotations.selectedId) {
            if (!annotationId || this.isIncognito || this.annotations.isBusy) {
                return;
            }

            const annotation = getAnnotationById(this, annotationId);
            if (!annotation) {
                return;
            }

            this.hideAnnotationPeek();
            this.selectAnnotation(annotation);
            this.annotations.isPanelOpen = false;
            this.annotations.enabled = true;
            this.annotations.isRepositioning = true;
            this.annotations.drag = null;
            clearAnnotationDraft(this);
            this.overlays.setActiveTool('annotation-move');
            syncAnnotationOverlays(this);
            this.resetControlFocus();
        };

        reader.finishAnnotationReposition = function finishAnnotationReposition() {
            this.annotations.isRepositioning = false;
            this.annotations.drag = null;
            this.annotations.isPanelOpen = true;
            this.annotations.enabled = true;
            clearAnnotationDraft(this);
            this.overlays.setActiveTool(this.annotations.activeTool || 'pin');
            syncAnnotationOverlays(this);
        };

        reader.resetAnnotationForm = function resetAnnotationForm() {
            this.annotations.form = {
                title: '',
                body: '',
                color: DEFAULT_ANNOTATION_COLOR
            };
        };

        reader.applyAnnotationUpdate = function applyAnnotationUpdate(annotation) {
            const normalizedAnnotation = {
                ...annotation,
                color: safeColor(annotation.color),
                anchor: annotation.anchor || {}
            };
            const existingIndex = this.annotations.items.findIndex((item) => item.id === normalizedAnnotation.id);
            const nextAnnotations = [...this.annotations.items];

            if (existingIndex >= 0) {
                nextAnnotations.splice(existingIndex, 1, normalizedAnnotation);
            } else {
                nextAnnotations.push(normalizedAnnotation);
            }

            this.annotations.items = sortAnnotations(nextAnnotations);
            this.annotations.loaded = true;
            syncAnnotationOverlays(this);
        };

        reader.createAnnotationFromOverlay = async function createAnnotationFromOverlay(kind, pageIndex, anchor) {
            if (this.isIncognito) {
                window.parker.showToast('Incognito Mode: Annotation changes are disabled.');
                return;
            }

            if (this.annotations.isBusy) {
                return;
            }

            this.annotations.isBusy = true;

            try {
                const response = await fetch(window.parker.route('annotations.create_comic_annotation', { comic_id: this.comicId }), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        page_index: pageIndex,
                        kind,
                        title: normalizeText(this.annotations.form.title),
                        body: normalizeText(this.annotations.form.body),
                        color: safeColor(this.annotations.form.color),
                        anchor
                    })
                });
                const payload = await readJsonResponse(response, 'Failed to save annotation');

                this.applyAnnotationUpdate(payload);
                this.resetAnnotationForm();
                this.selectAnnotation(payload.id);
                window.parker.showToast('Annotation saved');
            } catch (error) {
                console.error(error);
                window.parker.showToast(error.message || 'Failed to save annotation', 'error');
            } finally {
                this.annotations.isBusy = false;
            }
        };

        reader.selectAnnotation = function selectAnnotation(annotationOrId) {
            const annotationId = typeof annotationOrId === 'object' ? annotationOrId.id : annotationOrId;
            const annotation = this.annotations.items.find((item) => item.id === annotationId);
            if (!annotation) {
                return;
            }

            this.annotations.selectedId = annotation.id;
            this.annotations.editForm = {
                title: annotation.title || '',
                body: annotation.body || '',
                color: safeColor(annotation.color)
            };
            syncAnnotationOverlays(this);
        };

        reader.clearAnnotationSelection = function clearAnnotationSelection() {
            this.annotations.selectedId = null;
            this.annotations.isRepositioning = false;
            this.annotations.editForm = {
                title: '',
                body: '',
                color: DEFAULT_ANNOTATION_COLOR
            };
            syncAnnotationOverlays(this);
        };

        reader.saveAnnotationEdit = async function saveAnnotationEdit() {
            const annotationId = this.annotations.selectedId;
            if (!annotationId || this.isIncognito || this.annotations.isBusy) {
                return;
            }

            this.annotations.isBusy = true;

            try {
                const response = await fetch(window.parker.route('annotations.update_annotation', { annotation_id: annotationId }), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: normalizeText(this.annotations.editForm.title),
                        body: normalizeText(this.annotations.editForm.body),
                        color: safeColor(this.annotations.editForm.color)
                    })
                });
                const payload = await readJsonResponse(response, 'Failed to update annotation');

                this.applyAnnotationUpdate(payload);
                this.selectAnnotation(payload.id);
                window.parker.showToast('Annotation updated');
            } catch (error) {
                console.error(error);
                window.parker.showToast(error.message || 'Failed to update annotation', 'error');
            } finally {
                this.annotations.isBusy = false;
            }
        };

        reader.saveAnnotationPosition = async function saveAnnotationPosition(annotationId, anchor, originalAnchor) {
            if (!annotationId || this.isIncognito || this.annotations.isBusy) {
                return;
            }

            this.annotations.isBusy = true;

            try {
                const response = await fetch(window.parker.route('annotations.update_annotation', { annotation_id: annotationId }), {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ anchor })
                });
                const payload = await readJsonResponse(response, 'Failed to move annotation');

                this.applyAnnotationUpdate(payload);
                this.selectAnnotation(payload.id);
                window.parker.showToast('Annotation moved', undefined, 1000);
            } catch (error) {
                if (originalAnchor) {
                    updateAnnotationAnchor(this, annotationId, originalAnchor);
                }
                console.error(error);
                window.parker.showToast(error.message || 'Failed to move annotation', 'error');
            } finally {
                this.annotations.isBusy = false;
            }
        };

        reader.deleteAnnotation = async function deleteAnnotation(annotationId = this.annotations.selectedId) {
            if (!annotationId || this.isIncognito || this.annotations.isBusy) {
                return;
            }

            this.annotations.isBusy = true;

            try {
                const response = await fetch(window.parker.route('annotations.delete_annotation', { annotation_id: annotationId }), {
                    method: 'DELETE'
                });
                await readJsonResponse(response, 'Failed to delete annotation');

                this.annotations.items = this.annotations.items.filter((annotation) => annotation.id !== annotationId);
                if (this.annotations.peek.annotationId === annotationId) {
                    this.hideAnnotationPeek();
                }
                if (this.annotations.selectedId === annotationId) {
                    this.annotations.isRepositioning = false;
                    this.clearAnnotationSelection();
                }
                syncAnnotationOverlays(this);
                window.parker.showToast('Annotation deleted');
            } catch (error) {
                console.error(error);
                window.parker.showToast(error.message || 'Failed to delete annotation', 'error');
            } finally {
                this.annotations.isBusy = false;
            }
        };

        reader.getAnnotationKindLabel = function getAnnotationKindLabel(annotation) {
            if (!annotation) {
                return '';
            }

            return annotation?.kind === 'rectangle' ? 'Rectangle' : 'Pin';
        };

        reader.getAnnotationPageLabel = function getAnnotationPageLabel(annotation) {
            if (!annotation || !Number.isInteger(annotation.page_index)) {
                return '';
            }

            return `Page ${annotation.page_index + 1}`;
        };

        reader.getAnnotationDisplayTitle = function getAnnotationDisplayTitle(annotation) {
            return getAnnotationTitle(annotation);
        };

        reader.isAnnotationSelected = function isAnnotationSelected(annotation) {
            return !!annotation && annotation.id === this.annotations.selectedId;
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

        wrapReaderMethod(reader, 'loadInitData', (originalMethod) => async function loadInitDataWithAnnotations(...args) {
            this.initializeAnnotationLayerVisibility();
            await originalMethod.apply(this, args);
            await this.loadAnnotations();
        });

        wrapReaderMethod(reader, 'openBookmarks', (originalMethod) => async function openBookmarksWithAnnotationsClosed(...args) {
            if (this.annotations.isPanelOpen) {
                this.closeAnnotations();
            }

            return originalMethod.apply(this, args);
        });

        wrapReaderMethod(reader, 'openGoto', (originalMethod) => function openGotoWithAnnotationsClosed(...args) {
            if (this.annotations.isPanelOpen) {
                this.closeAnnotations();
            }

            return originalMethod.apply(this, args);
        });

        wrapReaderMethod(reader, 'handleKey', (originalMethod) => function handleKeyWithAnnotations(event) {
            if (handleAnnotationEscape(this, event)) {
                return;
            }

            if (keyStartedInAnnotationPanel(event)) {
                return;
            }

            if (this.annotations.peek.annotationId) {
                this.hideAnnotationPeek();
            }

            if (!event.ctrlKey && !event.metaKey && !event.altKey && (event.key === 'a' || event.key === 'A')) {
                event.preventDefault();
                this.toggleAnnotationMode();
                return;
            }

            return originalMethod.apply(this, [event]);
        });

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
