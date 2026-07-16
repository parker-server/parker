(() => {
    const DEFAULT_FILTERS = Object.freeze({
        transcode: false,
        grayscale: false,
        sharpen: false,
        brightness: 100,
        contrast: 100
    });

    const STORAGE_KEYS = Object.freeze({
        filters: 'readerFilters',
        readingMode: 'reader_readingMode',
        viewMode: 'reader_viewMode',
        readDirection: 'reader_readDirection',
        fitMode: 'reader_fitMode',
        doublePageOffset: 'reader_doublePageOffset',
        showSpineShadow: 'reader_showSpineShadow',
        uiLocked: 'reader_uiLocked'
    });

    function cloneDefaultFilters() {
        return { ...DEFAULT_FILTERS };
    }

    function loadBooleanPreference(key, fallback) {
        const rawValue = localStorage.getItem(key);
        return rawValue === null ? fallback : JSON.parse(rawValue);
    }

    function persistJsonPreference(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    }

    function loadFilters() {
        const savedFilters = localStorage.getItem(STORAGE_KEYS.filters);
        if (!savedFilters) {
            return cloneDefaultFilters();
        }

        try {
            return { ...cloneDefaultFilters(), ...JSON.parse(savedFilters) };
        } catch (error) {
            console.error('Failed to parse stored reader filters:', error);
            return cloneDefaultFilters();
        }
    }

    function buildReaderState(comicId) {
        return {
            currentTime: '',
            comicId,
            currentPage: 0,
            meta: { page_count: 0, next_comic_id: null, prev_comic_id: null },
            touchStartX: 0,
            touchStartY: 0,
            minSwipeDistance: 50,
            maxSwipeTime: 500,
            startTime: 0,
            readingMode: 'paged',
            showSettings: false,
            fitMode: 'contain',
            viewMode: 'single',
            readDirection: 'ltr',
            doublePageOffset: true,
            showSpineShadow: false,
            showGoto: false,
            gotoInputValue: 1,
            scrubberValue: 0,
            isScrubbing: false,
            isHoveringScrubber: false,
            tapVisible: false,
            uiLocked: false,
            isHoveringZone: false,
            isHoveringBar: false,
            isIncognito: false,
            contextType: null,
            contextId: null,
            pageMeta: {},
            filters: cloneDefaultFilters(),
            scrollTicking: false,
            restoringScroll: false,
            scrollRestoreTimeout: null
        };
    }

    function applyStoredReaderSettings(reader) {
        reader.readingMode = localStorage.getItem(STORAGE_KEYS.readingMode) || 'paged';
        reader.viewMode = localStorage.getItem(STORAGE_KEYS.viewMode) || 'single';
        reader.readDirection = localStorage.getItem(STORAGE_KEYS.readDirection) || 'ltr';
        reader.fitMode = localStorage.getItem(STORAGE_KEYS.fitMode) || 'contain';
        reader.doublePageOffset = loadBooleanPreference(STORAGE_KEYS.doublePageOffset, true);
        reader.showSpineShadow = loadBooleanPreference(STORAGE_KEYS.showSpineShadow, true);
        reader.uiLocked = loadBooleanPreference(STORAGE_KEYS.uiLocked, false);
        reader.filters = loadFilters();

        persistJsonPreference(STORAGE_KEYS.filters, reader.filters);
    }

    function registerReaderWatchers(reader) {
        reader.$watch('currentPage', (value) => {
            if (!reader.isScrubbing) {
                reader.scrubberValue = value;
            }

            reader.preloadContext();
        });

        reader.$watch('filters', (value) => persistJsonPreference(STORAGE_KEYS.filters, value));
        reader.$watch('readingMode', (value) => {
            localStorage.setItem(STORAGE_KEYS.readingMode, value);

            reader.$nextTick(() => {
                if (value === 'scroll') {
                    reader.syncScrollPage(reader.currentPage, { behavior: 'auto', persist: false });
                    return;
                }

                reader.focusReader();
            });
        });
        reader.$watch('viewMode', (value) => localStorage.setItem(STORAGE_KEYS.viewMode, value));
        reader.$watch('readDirection', (value) => localStorage.setItem(STORAGE_KEYS.readDirection, value));
        reader.$watch('fitMode', (value) => localStorage.setItem(STORAGE_KEYS.fitMode, value));
        reader.$watch('doublePageOffset', (value) => persistJsonPreference(STORAGE_KEYS.doublePageOffset, value));
        reader.$watch('showSpineShadow', (value) => persistJsonPreference(STORAGE_KEYS.showSpineShadow, value));
        reader.$watch('uiLocked', (value) => persistJsonPreference(STORAGE_KEYS.uiLocked, value));
    }

    function bindReaderMethods(reader) {
        for (const [key, value] of Object.entries(reader)) {
            if (typeof value !== 'function' || value.__readerBound) {
                continue;
            }

            const boundMethod = value.bind(reader);
            boundMethod.__readerBound = true;
            reader[key] = boundMethod;
        }
    }

    function buildComputedProperties() {
        return {
            isScrollMode: {
                enumerable: true,
                get() {
                    return this.readingMode === 'scroll';
                }
            },
            shouldShowUI: {
                enumerable: true,
                get() {
                    if (this.isScrollMode) {
                        return true;
                    }

                    return this.uiLocked
                        || this.tapVisible
                        || this.isHoveringZone
                        || this.showSettings
                        || this.showGoto
                        || this.isScrubbing
                        || this.isHoveringScrubber
                        || this.isHoveringBar;
                }
            },
            imageClasses: {
                enumerable: true,
                get() {
                    const isSmartSpread = this.viewMode === 'double' && this.pagesToDisplay.length === 1;
                    return this.viewMode === 'double' && !isSmartSpread
                        ? 'w-1/2 h-screen object-contain'
                        : 'w-full h-screen object-contain';
                }
            },
            imageStyles: {
                enumerable: true,
                get() {
                    const styles = {};

                    if (this.fitMode === 'contain') {
                        styles.height = '100vh';
                        styles.width = 'auto';
                    } else if (this.fitMode === 'width') {
                        styles.width = this.viewMode === 'double' ? '50vw' : '100vw';
                        styles.height = 'auto';
                    } else if (this.fitMode === 'height') {
                        styles.height = '100vh';
                        styles.width = 'auto';
                    }

                    styles.filter = `brightness(${this.filters.brightness}%) contrast(${this.filters.contrast}%)`;
                    return styles;
                }
            },
            scrollImageStyles: {
                enumerable: true,
                get() {
                    const styles = {
                        filter: `brightness(${this.filters.brightness}%) contrast(${this.filters.contrast}%)`
                    };

                    if (this.fitMode === 'contain') {
                        styles.width = 'auto';
                        styles.maxWidth = '100%';
                    } else {
                        styles.width = '100%';
                        styles.maxWidth = '100%';
                    }

                    return styles;
                }
            },
            pagesToDisplay: {
                enumerable: true,
                get() {
                    if (this.viewMode === 'single') {
                        return [{ index: this.currentPage }];
                    }

                    const firstPage = this.currentPage;
                    const secondPage = this.currentPage + 1;

                    if (this.isPageSolo(firstPage)) {
                        return [{ index: firstPage }];
                    }

                    if (secondPage >= this.meta.page_count) {
                        return [{ index: firstPage }];
                    }

                    if (this.isPageSolo(secondPage)) {
                        return [{ index: firstPage }];
                    }

                    return [{ index: firstPage }, { index: secondPage }];
                }
            }
        };
    }

    function buildReaderMethods() {
        return {
            init() {
                bindReaderMethods(this);
                this.updateClock();
                setInterval(() => { this.updateClock(); }, 1000);

                applyStoredReaderSettings(this);

                const params = new URLSearchParams(window.location.search);
                this.isIncognito = params.get('incognito') === 'true';
                this.contextType = params.get('context_type');
                this.contextId = params.get('context_id');

                if (this.isIncognito) {
                    window.parker.showToast('Incognito Mode: Progress will not be saved.');
                }

                this.loadInitData();
                registerReaderWatchers(this);
            },

            async loadInitData() {
                try {
                    const params = new URLSearchParams();
                    if (this.contextType) params.append('context_type', this.contextType);
                    if (this.contextId) params.append('context_id', this.contextId);

                    const response = await fetch(window.parker.route('reader.init', { comic_id: this.comicId }, params.toString()));

                    if (!response.ok) {
                        window.parker.showToast('Failed to init reader', 'error');
                        throw new Error('Failed to init reader');
                    }

                    this.meta = await response.json();

                    if (!this.isIncognito) {
                        const progressResponse = await fetch(window.parker.route('progress.comic_progress', { comic_id: this.comicId }));
                        const progress = await progressResponse.json();

                        if (progress.has_progress && !progress.completed) {
                            this.currentPage = progress.current_page;
                        }
                    }

                    this.preloadContext();

                    if (this.isScrollMode) {
                        this.$nextTick(() => {
                            this.syncScrollPage(this.currentPage, { behavior: 'auto', persist: false });
                        });
                    }
                } catch (error) {
                    console.error(error);
                }
            },

            preloadContext() {
                const bufferSize = this.viewMode === 'double' ? 4 : 2;

                for (let index = 1; index <= bufferSize; index += 1) {
                    const nextPage = this.currentPage + index;
                    if (nextPage < this.meta.page_count) {
                        this.preloadImage(nextPage);
                    }
                }

                for (let index = 1; index <= bufferSize; index += 1) {
                    const previousPage = this.currentPage - index;
                    if (previousPage >= 0) {
                        this.preloadImage(previousPage);
                    }
                }
            },

            preloadImage(index) {
                if (this.pageMeta[index] && this.pageMeta[index].loaded) {
                    return;
                }

                const image = new Image();
                image.src = this.getPageUrl(index);
                image.onload = () => {
                    this.pageMeta[index] = {
                        loaded: true,
                        isLandscape: image.width > image.height,
                        width: image.width,
                        height: image.height
                    };
                };
            },

            getPageUrl(index) {
                if (index === undefined || index < 0 || index >= this.meta.page_count) {
                    return '';
                }

                const url = window.parker.route('reader.comic_page', { comic_id: this.comicId, page_index: index });
                const params = new URLSearchParams();

                if (this.filters.transcode) params.append('webp', 'true');
                if (this.filters.sharpen) params.append('sharpen', 'true');
                if (this.filters.grayscale) params.append('grayscale', 'true');

                const filterKey = `${this.filters.transcode}-${this.filters.sharpen}-${this.filters.grayscale}-${this.filters.brightness}-${this.filters.contrast}`;
                params.append('v', filterKey);

                return `${url}?${params.toString()}`;
            },

            isPageSolo(index) {
                if (this.doublePageOffset && index === 0) {
                    return true;
                }

                return !!(this.pageMeta[index] && this.pageMeta[index].isLandscape);
            },

            focusReader() {
                this.$el.focus();
            },

            resetControlFocus() {
                const activeElement = document.activeElement;
                if (activeElement instanceof HTMLElement && activeElement !== this.$el && this.$el.contains(activeElement)) {
                    activeElement.blur();
                }

                this.focusReader();
            },

            setReadingMode(mode) {
                if (!['paged', 'scroll'].includes(mode) || mode === this.readingMode) {
                    return;
                }

                this.showSettings = false;
                this.readingMode = mode;
                window.parker.showToast(mode === 'scroll' ? 'Long View enabled' : 'Paged View enabled');
            },

            getScrollPageElements() {
                return Array.from(this.$el.querySelectorAll('[data-scroll-page-index]'));
            },

            getScrollPageElement(index) {
                return this.$el.querySelector(`[data-scroll-page-index="${index}"]`);
            },

            getScrollViewportTargetTop() {
                const containerRect = this.$el.getBoundingClientRect();
                const toolbar = this.$el.querySelector('.reader-toolbar');
                const toolbarHeight = toolbar ? toolbar.getBoundingClientRect().height : 0;

                return {
                    containerTop: containerRect.top,
                    targetTop: containerRect.top + toolbarHeight + 8
                };
            },

            getScrollTopForPage(index) {
                const target = this.getScrollPageElement(index);
                if (!target) {
                    return null;
                }

                const targetRect = target.getBoundingClientRect();
                if (targetRect.height < 8) {
                    return null;
                }

                const { targetTop } = this.getScrollViewportTargetTop();
                const scrollTop = this.$el.scrollTop + (targetRect.top - targetTop);
                return Math.max(0, scrollTop);
            },

            applyScrollPagePosition(index, { retries = 40 } = {}) {
                const scrollTop = this.getScrollTopForPage(index);
                if (scrollTop === null) {
                    if (retries > 0) {
                        window.setTimeout(() => {
                            this.applyScrollPagePosition(index, { retries: retries - 1 });
                        }, 75);
                    }
                    return;
                }

                this.restoringScroll = true;
                if (this.scrollRestoreTimeout) {
                    window.clearTimeout(this.scrollRestoreTimeout);
                }

                const targetScrollTop = Math.round(scrollTop);
                this.$el.scrollTop = targetScrollTop;

                window.requestAnimationFrame(() => {
                    const target = this.getScrollPageElement(index);
                    const expectedTop = this.getScrollViewportTargetTop().targetTop;
                    const targetTopDelta = target
                        ? Math.abs(target.getBoundingClientRect().top - expectedTop)
                        : Number.POSITIVE_INFINITY;
                    const scrollDelta = Math.abs(this.$el.scrollTop - targetScrollTop);

                    if ((scrollDelta > 4 || targetTopDelta > 12) && retries > 0) {
                        window.setTimeout(() => {
                            this.applyScrollPagePosition(index, { retries: retries - 1 });
                        }, 75);
                        return;
                    }

                    this.scrollRestoreTimeout = window.setTimeout(() => {
                        this.restoringScroll = false;
                        this.syncCurrentPageFromScroll(false);
                    }, 120);
                });
            },

            jumpToScrollPage(index, { persist = true, retries = 40 } = {}) {
                if (!this.isScrollMode || this.meta.page_count <= 0) {
                    return;
                }

                this.resetControlFocus();

                const boundedIndex = Math.max(0, Math.min(index, this.meta.page_count - 1));
                const changed = boundedIndex !== this.currentPage;
                this.currentPage = boundedIndex;

                if (persist && changed) {
                    this.updateProgress();
                }

                this.applyScrollPagePosition(boundedIndex, { retries });
                this.$nextTick(() => {
                    window.requestAnimationFrame(() => {
                        this.applyScrollPagePosition(boundedIndex, { retries });
                    });
                });
            },

            syncScrollPage(index, { behavior = 'auto', persist = true } = {}) {
                void behavior;
                this.jumpToScrollPage(index, { persist });
            },

            handleScroll() {
                if (!this.isScrollMode || this.restoringScroll) {
                    return;
                }

                if (this.scrollTicking) {
                    return;
                }

                this.scrollTicking = true;
                requestAnimationFrame(() => {
                    this.scrollTicking = false;
                    this.syncCurrentPageFromScroll();
                });
            },

            syncCurrentPageFromScroll(persist = true) {
                if (!this.isScrollMode) {
                    return;
                }

                const pageElements = this.getScrollPageElements();
                if (pageElements.length === 0) {
                    return;
                }

                const containerRect = this.$el.getBoundingClientRect();
                const threshold = containerRect.top + 96;
                let activeIndex = Number.parseInt(
                    pageElements[pageElements.length - 1].dataset.scrollPageIndex,
                    10,
                );

                for (const pageElement of pageElements) {
                    const rect = pageElement.getBoundingClientRect();
                    if (rect.bottom > threshold) {
                        activeIndex = Number.parseInt(pageElement.dataset.scrollPageIndex, 10);
                        break;
                    }
                }

                if (Number.isNaN(activeIndex) || activeIndex === this.currentPage) {
                    return;
                }

                this.currentPage = activeIndex;
                if (persist) {
                    this.updateProgress();
                }
            },

            toggleViewMode() {
                if (this.isScrollMode) {
                    return;
                }

                this.viewMode = this.viewMode === 'single' ? 'double' : 'single';
                window.parker.showToast(this.viewMode === 'double' ? 'Double Page Mode' : 'Single Page Mode');
            },

            nextPage() {
                if (this.isScrollMode) {
                    const nextPageIndex = Math.min(this.currentPage + 1, this.meta.page_count - 1);
                    if (nextPageIndex !== this.currentPage) {
                        this.jumpToScrollPage(nextPageIndex);
                    } else if (this.meta.next_comic_id) {
                        window.parker.showToast('End of Book. Press ] for next issue.');
                    }
                    return;
                }

                const step = this.pagesToDisplay.length;

                if (this.currentPage + step < this.meta.page_count) {
                    this.currentPage += step;
                    this.updateProgress();
                } else if (this.meta.next_comic_id) {
                    window.parker.showToast('End of Book. Press ] for next issue.');
                }
            },

            prevPage() {
                if (this.isScrollMode) {
                    const previousPageIndex = Math.max(this.currentPage - 1, 0);
                    if (previousPageIndex !== this.currentPage) {
                        this.jumpToScrollPage(previousPageIndex);
                    }
                    return;
                }

                if (this.viewMode === 'single') {
                    if (this.currentPage > 0) {
                        this.currentPage -= 1;
                        this.updateProgress();
                    }
                    return;
                }

                const previousPage = this.currentPage - 1;
                if (previousPage < 0) {
                    return;
                }

                if (this.isPageSolo(previousPage)) {
                    this.currentPage -= 1;
                    this.updateProgress();
                    return;
                }

                const lookbackPage = this.currentPage - 2;
                if (lookbackPage >= 0 && this.isPageSolo(lookbackPage)) {
                    this.currentPage -= 1;
                    this.updateProgress();
                    return;
                }

                if (lookbackPage < 0) {
                    this.currentPage = 0;
                    this.updateProgress();
                    return;
                }

                this.currentPage -= 2;
                this.updateProgress();
            },

            goToBook(id) {
                if (!id) {
                    return;
                }

                let url = window.parker.route('pages.reader', { comic_id: id });
                const params = new URLSearchParams();

                if (this.contextType) params.append('context_type', this.contextType);
                if (this.contextId) params.append('context_id', this.contextId);
                if (this.isIncognito) params.append('incognito', 'true');

                const queryString = params.toString();
                if (queryString) {
                    url += `?${queryString}`;
                }

                window.location.href = url;
            },

            exitReader() {
                if (this.contextType === 'reading_list' && this.contextId) {
                    window.location.href = window.parker.route('pages.reading_list_detail', { reading_list_id: this.contextId });
                    return;
                }

                if (this.contextType === 'collection' && this.contextId) {
                    window.location.href = window.parker.route('pages.collection_detail', { collection_id: this.contextId });
                    return;
                }

                if (this.contextType === 'pull_list' && this.contextId) {
                    window.location.href = window.parker.route('pages.pull_list_detail', { list_id: this.contextId });
                    return;
                }

                if (this.contextType === 'series' && this.contextId) {
                    window.location.href = window.parker.route('pages.series_detail', { series_id: this.contextId });
                    return;
                }

                if (this.contextType === 'volume' && this.contextId) {
                    window.location.href = window.parker.route('pages.volume_detail', { volume_id: this.contextId });
                    return;
                }

                window.location.href = window.parker.route('pages.comic_detail', { comic_id: this.comicId });
            },

            resetFilters() {
                this.filters = cloneDefaultFilters();
            },

            async updateProgress() {
                if (this.isIncognito) {
                    return;
                }

                const params = new URLSearchParams();
                if (this.contextType) params.append('context_type', this.contextType);
                if (this.contextId) params.append('context_id', this.contextId);

                fetch(window.parker.route('progress.comic_progress', { comic_id: this.comicId }, params.toString()), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ current_page: this.currentPage, total_pages: this.meta.page_count })
                });

                if (this.currentPage === this.meta.page_count - 1) {
                    fetch(`/api/progress/${this.comicId}/mark-read`, { method: 'POST' });
                }
            },

            toggleFullscreen() {
                if (!document.fullscreenElement) {
                    document.documentElement.requestFullscreen();
                    void setTimeout(() => { window.parker.showToast('Fullscreen mode on', 'info'); }, 500);
                    return;
                }

                document.exitFullscreen();
            },

            handleKey(event) {
                if (this.showGoto) {
                    if (event.key === 'Escape') {
                        this.closeGoto();
                    }
                    return;
                }

                if (['INPUT', 'TEXTAREA'].includes(event.target.tagName) && event.target.type === 'text') {
                    return;
                }

                if (event.ctrlKey || event.metaKey || event.altKey) {
                    return;
                }

                switch (event.key) {
                    case 'ArrowRight':
                    case ' ':
                        event.preventDefault();
                        this.nextPage();
                        break;
                    case 'ArrowLeft':
                        this.prevPage();
                        break;
                    case 'f':
                    case 'F':
                        this.toggleFullscreen();
                        break;
                    case ']':
                        this.goToBook(this.meta.next_comic_id);
                        break;
                    case '[':
                        this.goToBook(this.meta.prev_comic_id);
                        break;
                    case 'd':
                    case 'D':
                        this.toggleViewMode();
                        break;
                    case 'g':
                    case 'G':
                        event.preventDefault();
                        this.openGoto();
                        break;
                    case 'h':
                    case 'H':
                        this.toggleUiLock();
                        break;
                    case 'm':
                    case 'M':
                        this.toggleMangaMode();
                        break;
                    case 'Escape':
                        if (this.showSettings) {
                            this.showSettings = false;
                            break;
                        }

                        this.exitReader();
                        break;
                }
            },

            openGoto() {
                this.showGoto = true;
                this.gotoInputValue = this.currentPage + 1;

                this.$nextTick(() => {
                    this.$refs.gotoInput.focus();
                    this.$refs.gotoInput.select();
                });
            },

            closeGoto() {
                this.showGoto = false;
                this.$el.focus();
            },

            submitGoto() {
                let page = Number.parseInt(this.gotoInputValue, 10);

                if (Number.isNaN(page)) {
                    return;
                }

                if (page < 1) page = 1;
                if (page > this.meta.page_count) page = this.meta.page_count;

                if (this.isScrollMode) {
                    this.syncScrollPage(page - 1, { behavior: 'auto' });
                } else {
                    this.currentPage = page - 1;
                    this.updateProgress();
                }
                this.closeGoto();
            },

            updateScrubberUI() {
                this.isScrubbing = true;
            },

            finishScrub() {
                this.isScrubbing = false;

                const targetPage = Number.parseInt(this.scrubberValue, 10);
                if (Number.isNaN(targetPage)) {
                    return;
                }

                if (this.isScrollMode) {
                    this.syncScrollPage(targetPage, { behavior: 'auto' });
                    return;
                }

                if (targetPage !== this.currentPage) {
                    this.currentPage = targetPage;
                    this.updateProgress();
                }
            },

            toggleUiLock() {
                this.uiLocked = !this.uiLocked;
                window.parker.showToast(this.uiLocked ? 'UI Pinned' : 'UI Auto-Hide');
            },

            getContextLabel(contextType) {
                if (contextType === 'pull_list') return 'Pull List';
                if (contextType === 'reading_list') return 'Reading List';
                if (contextType === 'collection') return 'Collection';
                if (contextType === 'series') return 'Series';
                return 'Volume';
            },

            handleZoneClick(zone) {
                if (zone === 'center') {
                    if (this.uiLocked) {
                        window.parker.showToast('UI is Pinned. Use Eye icon to unlock.');
                        return;
                    }

                    this.tapVisible = !this.tapVisible;
                    return;
                }

                if (this.readDirection === 'rtl') {
                    if (zone === 'left') this.nextPage();
                    else this.prevPage();
                    return;
                }

                if (zone === 'left') this.prevPage();
                else this.nextPage();
            },

            toggleMangaMode() {
                if (this.isScrollMode) {
                    return;
                }

                this.readDirection = this.readDirection === 'ltr' ? 'rtl' : 'ltr';
                window.parker.showToast(this.readDirection === 'rtl' ? 'Manga Mode (RTL)' : 'Western Mode (LTR)');
            },

            handleTouchStart(event) {
                if (this.isScrollMode) {
                    return;
                }

                this.touchStartX = event.changedTouches[0].screenX;
                this.touchStartY = event.changedTouches[0].screenY;
                this.startTime = new Date().getTime();
            },

            handleTouchEnd(event) {
                if (this.isScrollMode) {
                    return;
                }

                const touchEndX = event.changedTouches[0].screenX;
                const touchEndY = event.changedTouches[0].screenY;
                const elapsedTime = new Date().getTime() - this.startTime;

                if (elapsedTime > this.maxSwipeTime) {
                    return;
                }

                const diffX = touchEndX - this.touchStartX;
                const diffY = touchEndY - this.touchStartY;

                if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > this.minSwipeDistance) {
                    if (diffX > 0) {
                        this.onSwipeRight();
                    } else {
                        this.onSwipeLeft();
                    }
                }
            },

            onSwipeRight() {
                if (this.readDirection === 'rtl') {
                    this.nextPage();
                } else {
                    this.prevPage();
                }
            },

            onSwipeLeft() {
                if (this.readDirection === 'rtl') {
                    this.prevPage();
                } else {
                    this.nextPage();
                }
            },

            updateClock() {
                this.currentTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            }
        };
    }

    window.createReader = ({ comicId }) => {
        const reader = buildReaderState(comicId);
        Object.assign(reader, buildReaderMethods());
        Object.defineProperties(reader, buildComputedProperties());
        return reader;
    };
})();
