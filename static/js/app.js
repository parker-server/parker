/**
 * 1. GLOBAL FETCH WRAPPER (For Alpine.js & Custom JS)
 * Automatically injects JWT token and handles 401 Redirects.
 */
(function() {

    const storage = window.parker.storage;
    const originalFetch = window.fetch;
    let isRefreshing = false;
    let refreshPromise = null;
    let refreshTimer = null;
    let failedQueue = [];
    const REFRESH_SKEW_SECONDS = 90;
    const IDLE_TIMEOUT_SECONDS = 2 * 60 * 60;
    const LAST_ACTIVITY_KEY = 'lastActivityAt';

    const processQueue = (error, token = null) => {
        failedQueue.forEach(prom => {
            if (error) {
                prom.reject(error);
            } else {
                prom.resolve(token);
            }
        });
        failedQueue = [];
    };

    const appUrl = (path) => {
        if (window.parker && typeof window.parker.url === 'function') {
            return window.parker.url(path);
        }
        return path;
    };

    const getJwtPayload = (token) => {
        if (!token || token.split('.').length < 2) return null;

        try {
            const payload = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
            const paddedPayload = payload.padEnd(payload.length + ((4 - payload.length % 4) % 4), '=');
            return JSON.parse(atob(paddedPayload));
        } catch (error) {
            return null;
        }
    };

    const getTokenSecondsRemaining = (token) => {
        const payload = getJwtPayload(token);
        if (!payload || !payload.exp) return 0;
        return payload.exp - Math.floor(Date.now() / 1000);
    };

    const nowSeconds = () => Math.floor(Date.now() / 1000);

    const getLastActivityAt = () => {
        const stored = parseInt(storage.getString(LAST_ACTIVITY_KEY), 10);
        return Number.isFinite(stored) && stored > 0 ? stored : nowSeconds();
    };

    const setLastActivityAt = (timestamp = nowSeconds()) => {
        storage.setString(LAST_ACTIVITY_KEY, String(timestamp));
    };

    const isIdleExpired = () => {
        const refreshToken = storage.getString('refresh_token');
        return !!refreshToken && (nowSeconds() - getLastActivityAt()) > IDLE_TIMEOUT_SECONDS;
    };

    const setAccessCookie = (token, lifetimeInSeconds) => {
        document.cookie = `access_token=${token}; path=/; max-age=${lifetimeInSeconds}; SameSite=Lax`;
    };

    const clearSession = () => {
        storage.remove('token');
        storage.remove('refresh_token');
        storage.remove(LAST_ACTIVITY_KEY);
        document.cookie = 'access_token=; path=/; max-age=0; SameSite=Lax';
    };

    const markActivity = () => {
        if (!storage.getString('refresh_token') || isIdleExpired()) return;
        const now = nowSeconds();
        if ((now - getLastActivityAt()) < 15) return;
        setLastActivityAt(now);
    };

    const scheduleRefresh = () => {
        if (refreshTimer) {
            clearTimeout(refreshTimer);
            refreshTimer = null;
        }

        const token = storage.getString('token');
        const refreshToken = storage.getString('refresh_token');
        if (!token || !refreshToken) return;

        if (isIdleExpired()) {
            clearSession();
            return;
        }

        const secondsRemaining = getTokenSecondsRemaining(token);
        const refreshInMs = Math.max((secondsRemaining - REFRESH_SKEW_SECONDS) * 1000, 10 * 1000);
        refreshTimer = window.setTimeout(() => {
            refreshSession({ force: true }).catch(() => {});
        }, refreshInMs);
    };

    const refreshSession = async ({ force = false } = {}) => {
        if (isIdleExpired()) {
            clearSession();
            throw new Error('Session idle timeout');
        }

        const token = storage.getString('token');
        const secondsRemaining = getTokenSecondsRemaining(token);

        if (!force && token && secondsRemaining > REFRESH_SKEW_SECONDS) {
            scheduleRefresh();
            return token;
        }

        const refreshToken = storage.getString('refresh_token');
        if (!refreshToken) {
            return null;
        }

        if (refreshPromise) {
            return refreshPromise;
        }

        refreshPromise = (async () => {
            const refreshRes = await originalFetch(appUrl('/api/auth/refresh'), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_token: refreshToken })
            });

            if (!refreshRes.ok) {
                clearSession();
                throw new Error('Refresh failed');
            }

            const data = await refreshRes.json();
            storage.setString('token', data.access_token);
            storage.setString('refresh_token', data.refresh_token);
            setAccessCookie(data.access_token, data.lifetime_in_seconds);
            scheduleRefresh();

            return data.access_token;
        })().finally(() => {
            refreshPromise = null;
        });

        return refreshPromise;
    };

    const shouldPrepareNavigation = (anchor) => {
        if (!anchor || !anchor.href || anchor.target || anchor.hasAttribute('download')) {
            return false;
        }

        const url = new URL(anchor.href, window.location.href);
        if (url.origin !== window.location.origin) return false;
        if (url.pathname.includes('/login')) return false;
        if (url.pathname.startsWith(appUrl('/api/'))) return false;
        return true;
    };

    const prepareNavigation = async (event) => {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }

        const anchor = event.target instanceof Element ? event.target.closest('a') : null;
        if (!shouldPrepareNavigation(anchor)) return;

        const token = storage.getString('token');
        const refreshToken = storage.getString('refresh_token');
        if (!refreshToken || getTokenSecondsRemaining(token) > REFRESH_SKEW_SECONDS) {
            return;
        }

        event.preventDefault();

        try {
            await refreshSession({ force: true });
            window.location.href = anchor.href;
        } catch (error) {
            window.location.href = appUrl('/login');
        }
    };

    window.parker = {
        ...(window.parker || {}),
        auth: {
            refreshSession,
            scheduleRefresh,
            clearSession,
            setAccessCookie,
            markActivity,
            isIdleExpired,
            idleTimeoutSeconds: IDLE_TIMEOUT_SECONDS
        }
    };

    window.fetch = async function(url, options = {}) {

        options.headers = options.headers || {};

        // Helper to inject token
        const injectToken = (token) => {
            if (options.headers instanceof Headers) {
                options.headers.set('Authorization', `Bearer ${token}`);
            } else {
                options.headers['Authorization'] = `Bearer ${token}`;
            }
        };

        // Initial Token Attempt
        const token = storage.getString('token');
        if (token) { injectToken(token); }

        try {
            const response = await originalFetch(url, options);

            // Handle Unauthorized (401)
            if (response.status === 401) {

                // If we are already refreshing, queue this request
                if (isRefreshing) {
                    return new Promise((resolve, reject) => {
                        failedQueue.push({ resolve, reject });
                    }).then(newToken => {
                        injectToken(newToken);
                        return originalFetch(url, options);
                    }).catch(err => {
                        return response; // Return original 401 if refresh fails
                    });
                }

                // Start Refresh Logic
                const refreshToken = storage.getString('refresh_token');

                // If no refresh token, or if we are calling the login/refresh endpoints themselves, abort
                const requestUrl = typeof url === 'string' ? url : (url && url.url) || String(url);
                if (!refreshToken || requestUrl.includes('/token') || requestUrl.includes('/refresh')) {

                    // Actual logout logic
                    console.warn('Session expired. Redirecting to login.');
                    clearSession();

                    // Avoid infinite reload loops if already on login
                    if (!window.location.pathname.includes('/login')) {
                        window.location.href = appUrl('/login');
                    }
                    return response;
                }

                isRefreshing = true;

                try {
                    const newToken = await refreshSession({ force: true });
                    processQueue(null, newToken);
                    injectToken(newToken);
                    return originalFetch(url, options);
                } catch (refreshErr) {
                    // Refresh failed completely - Force Logout
                    processQueue(refreshErr, null);
                    clearSession();
                    window.location.href = appUrl('/login');

                    return response;
                } finally {
                    isRefreshing = false;
                }
            }

            return response;
        } catch (error) {
            console.error('Network Error:', error);
            throw error;
        }
    };

    document.addEventListener('click', prepareNavigation, true);
    document.addEventListener('click', markActivity, true);
    document.addEventListener('keydown', markActivity, true);
    document.addEventListener('touchstart', markActivity, true);
    document.addEventListener('scroll', markActivity, true);
    window.addEventListener('focus', () => refreshSession().catch(() => {}));
    window.addEventListener('pageshow', () => refreshSession().catch(() => {}));
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            refreshSession().catch(() => {});
        }
    });
    if (storage.getString('refresh_token') && !storage.getString(LAST_ACTIVITY_KEY)) {
        setLastActivityAt();
    }
    scheduleRefresh();
})();

document.addEventListener('alpine:init', () => {

    Alpine.data('smartRail', (list) => ({
        items: [],
        loading: true,
        listId: list.id,
        title: list.name,
        icon: list.icon || '⚡', // Default icon if missing

        async init() {
            // Intersection Observer (Optional Performance Boost):
            // Only fetch when the user actually scrolls down to this rail.
            // For now, we'll just fetch immediately on load.
            this.fetchItems();
        },

        async fetchItems() {
            try {
                // Call the "Auto-Fire" endpoint we made earlier
                const res = await fetch(window.parker.url(`/api/smart-lists/${this.listId}/items?limit=15`));
                if (res.ok) {
                    const data = await res.json();
                    this.items = data.items;
                }
            } catch (e) {
                console.error(`Failed to load smart list ${this.listId}`, e);
            } finally {
                this.loading = false;
            }
        }
    }));


    // Quick Search Component Logic
    Alpine.data('quickSearch', () => ({
        query: '',
        results: {},
        isOpen: false,
        loading: false,
        creatorRoleFields: ['writer', 'penciller', 'inker', 'colorist', 'letterer', 'editor', 'cover_artist'],

        get hasResults() {
            return Object.values(this.results).some(arr => arr && arr.length > 0);
        },

        personSearchHref(item) {
            const name = item?.name?.trim();
            if (!name) {
                return window.parker.route('pages.search');
            }

            return window.parker.searchHandoff.buildUrl({
                match: 'any',
                filters: this.creatorRoleFields.map((field) => ({
                    field,
                    operator: 'equal',
                    value: [name]
                }))
            });
        },

        async fetchResults() {
            if (this.query.length < 2) {
                this.isOpen = false;
                return;
            }
            this.loading = true;
            try {
                const res = await fetch(`/api/search/quick?q=${encodeURIComponent(this.query)}`);
                if (res.ok) {
                    this.results = await res.json();
                    this.isOpen = true;
                }
            } catch (e) {
                console.error(e);
            } finally {
                this.loading = false;
            }
        },

        close(immediate = false) {
            if (immediate) {
                this.isOpen = false;
                return;
            }

            setTimeout(() => { this.isOpen = false; }, 200);
        }
    }));

    Alpine.store('batch', {

        items: new Set(), // Stores "comic:1", "series:2", etc.
        readCount: 0, // Tracks how many selected items are already read

        // Helpers
        get count() { return this.items.size; },
        get active() { return this.items.size > 0; },

        // Smart Getter: Are ALL selected items currently read?
        get allRead() { return this.count > 0 && this.readCount === this.count; },

        // Helper: Check if specific item is selected
        has(id, type = 'comic') {
            return this.items.has(`${type}:${id}`);
        },

        // Helper: Toggle selection
        toggle(id, type = 'comic', isRead = false) {
            const key = `${type}:${id}`;
            if (this.items.has(key)) {
                this.items.delete(key);
                if (isRead) this.readCount--; // Decrement if removing a read item
            }
            else {
                this.items.add(key);
                if (isRead) this.readCount++; // Increment if adding a read item
            }
        },

        // Helper: Sorts selection into API-ready arrays
        get payload() {

            const result = { comic_ids: [], series_ids: [], volume_ids: [] };

            this.items.forEach(key => {
                const [type, id] = key.split(':');
                const numericId = parseInt(id);

                if (type === 'comic') result.comic_ids.push(numericId);
                else if (type === 'series') result.series_ids.push(numericId);
                else if (type === 'volume') result.volume_ids.push(numericId);
                // Possible future type handling
            });
            return result;
        },

        clear() { this.items.clear(); this.readCount = 0; }
    });


    // ... (ScannerStatus component should also be here ideally) ...
});


// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
    // Global shortcuts (Ctrl/Cmd + key)
    if (e.ctrlKey || e.metaKey) {
        switch(e.key) {
            case 'k':
                e.preventDefault();
                document.querySelector('input[type="search"]')?.focus();
                break;
            case '/':
                e.preventDefault();
                window.location.href = '/search';
                break;
        }
    }
    
    // Escape key
    if (e.key === 'Escape') {
        // Close modals
        document.querySelectorAll('.modal').forEach(modal => {
            modal.classList.add('hidden');
        });
    }
});

// Image lazy loading error handler
document.addEventListener('error', function(e) {
    if (e.target.tagName === 'IMG') {
        // console.error('Image failed to load:', e.target.src);
        // Simple gray placeholder
        e.target.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="100" height="150"%3E%3Crect width="100" height="150" fill="%23374151"/%3E%3Ctext x="50" y="75" text-anchor="middle" fill="%239ca3af" font-family="sans-serif"%3ENo Image%3C/text%3E%3C/svg%3E';
    }
}, true);


(() => {

    // Toast notifications
    const showToast = (message, type = 'info') => {

        const container = document.getElementById('toast-container');
        if (!container) return; // Guard against missing container on login page

        const toast = document.createElement('div');

        const colors = {
            info: 'bg-gray-800',
            success: 'bg-green-600',
            error: 'bg-red-600',
            warning: 'bg-yellow-600'
        };

        toast.className = `${colors[type]} text-white px-6 py-3 rounded-lg shadow-lg transition-opacity`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // Debounce utility
    const debounce = (func, wait) => {

        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // Format file size
    const formatFileSize = (bytes) => {

        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }

    const formatBytes = (bytes, decimals = 2) => {

        if (!bytes) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    // Format date
    const formatDate = (dateString) => {

        const date = new Date(dateString);
        const now = new Date();
        const diff = now - date;
        const days = Math.floor(diff / (1000 * 60 * 60 * 24));

        if (days === 0) return 'Today';
        if (days === 1) return 'Yesterday';
        if (days < 7) return `${days} days ago`;
        if (days < 30) return `${Math.floor(days / 7)} weeks ago`;
        if (days < 365) return `${Math.floor(days / 30)} months ago`;
        return date.toLocaleDateString();
    }

    // Storage & Prefs
    const storage = window.parker.storage;

    /**
     * Generic Pagination Logic
     * @param {string} routeName - The Parker route name (e.g., 'libraries.series')
     * @param {Function} getRouteParams - Callback to get path params (e.g. () => ({ library_id: 1 }))
     * @param {object} config - Optional overrides { pageSize, mode }
     */
    const paginationMixin = (routeName, getRouteParams, config = {}) => {

        return {
            // State
            items: [],
            loading: true,
            page: 1,
            size: config.pageSize || 20,
            total: 0,
            mode: config.mode || 'infinite',

            // Actions
            async loadItems(append = false) {
                this.loading = true;
                try {
                    // 1. Resolve Params
                    const params = getRouteParams ? getRouteParams.call(this) : {};

                    // 2. Build URL
                    let qs = `page=${this.page}&size=${this.size}`;

                    // 3. Extra Query Params (Filters, Sorts)
                    if (config.getQueryParams) {
                        const extra = config.getQueryParams.call(this);
                        // Convert object to query string (e.g. {type: 'annual'} -> "&type=annual")
                        const usp = new URLSearchParams(extra);
                        qs += `&${usp.toString()}`;
                    }

                    const url = window.parker.route(routeName, params, qs);

                    // 4. Fetch
                    const res = await fetch(url);
                    const data = await res.json();

                    // 5. Update State
                    if (append) {
                        this.items = [...this.items, ...data.items];
                    } else {
                        this.items = data.items;
                        if (this.items.length > 0 && !append) {
                            // Only scroll up on full page loads, not infinite appends
                            // (Logic handled by 'append' flag usually implies infinite)
                            if(this.mode === 'classic') window.scrollTo({ top: 0, behavior: 'smooth' });
                        }
                    }
                    this.total = data.total;

                } catch (e) {
                    console.error("Pagination Error:", e);
                    if(window.parker) window.parker.showToast("Failed to load items", "error");
                } finally {
                    this.loading = false;

                    // Re-arm Infinite Scroll
                    if (this.mode === 'infinite') {
                        this.$nextTick(() => this.setupObserver());
                    }
                }
            },

            // --- Controls ---

            changePage(newPage) {
                if (newPage < 1) return;
                const maxPage = this.maxPage ? this.maxPage() : 1;
                if (newPage > maxPage) return;

                this.page = newPage;
                this.loadItems(false); // Replace
            },

            setupObserver() {
                // Requires an element with x-ref="loadSentinel"
                if (this.$refs.loadSentinel) {
                    // Disconnect old observer if saved? (Alpine usually handles cleanup,
                    // but for strict safety we rely on the closure)
                    const observer = new IntersectionObserver((entries) => {
                        if (entries[0].isIntersecting && !this.loading) {
                            this.loadMore();
                        }
                    }, { rootMargin: '200px' });

                    observer.observe(this.$refs.loadSentinel);
                }
            },

            loadMore() {
                const maxPage = this.maxPage();
                if (this.page < maxPage) {
                    this.page++;
                    this.loadItems(true); // Append
                }
            },

            maxPage() {
                return Math.ceil(this.total / this.size) || 1;
            }
        };
    }




    const prefs = {
        getViewMode() { return storage.get('viewMode', 'grid'); },
        setViewMode(mode) { storage.set('viewMode', mode); },
        getFitMode() { return storage.get('fitMode', 'contain'); },
        setFitMode(mode) { storage.set('fitMode', mode); }
    };

    const SEARCH_HANDOFF_PREFIX = 'parker.searchHandoff.';
    const SEARCH_HANDOFF_MAX_QUERY_LENGTH = 1800;
    const SEARCH_HANDOFF_TTL_MS = 1000 * 60 * 60 * 6;

    const safeSessionStorage = {
        get(key) {
            try {
                return window.sessionStorage.getItem(key);
            } catch (e) {
                console.error('Error reading from sessionStorage:', e);
                return null;
            }
        },
        set(key, value) {
            try {
                window.sessionStorage.setItem(key, value);
                return true;
            } catch (e) {
                console.error('Error writing to sessionStorage:', e);
                return false;
            }
        },
        remove(key) {
            try {
                window.sessionStorage.removeItem(key);
            } catch (e) {
                console.error('Error removing from sessionStorage:', e);
            }
        },
        keys() {
            try {
                return Object.keys(window.sessionStorage);
            } catch (e) {
                console.error('Error listing sessionStorage keys:', e);
                return [];
            }
        }
    };

    const searchHandoff = {
        maxQueryLength: SEARCH_HANDOFF_MAX_QUERY_LENGTH,

        cleanup() {
            const now = Date.now();
            safeSessionStorage.keys()
                .filter(key => key.startsWith(SEARCH_HANDOFF_PREFIX))
                .forEach((key) => {
                    try {
                        const raw = safeSessionStorage.get(key);
                        if (!raw) {
                            safeSessionStorage.remove(key);
                            return;
                        }

                        const payload = JSON.parse(raw);
                        if (!payload.createdAt || (now - payload.createdAt) > SEARCH_HANDOFF_TTL_MS) {
                            safeSessionStorage.remove(key);
                        }
                    } catch (e) {
                        safeSessionStorage.remove(key);
                    }
                });
        },

        buildUrl(payload = {}) {
            const query = new URLSearchParams();

            if (payload.match) query.set('match', payload.match);
            if (payload.sortBy) query.set('sort_by', payload.sortBy);
            if (payload.sortOrder) query.set('sort_order', payload.sortOrder);
            if (payload.limit) query.set('limit', String(payload.limit));
            if (payload.libraryId) query.set('library_id', String(payload.libraryId));
            if (payload.filters && payload.filters.length > 0) {
                query.set('filters', JSON.stringify(payload.filters));
            }

            const rawQuery = query.toString();
            if (rawQuery.length <= this.maxQueryLength) {
                return window.parker.route('pages.search', {}, rawQuery);
            }

            this.cleanup();

            const key = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
            const stored = safeSessionStorage.set(
                `${SEARCH_HANDOFF_PREFIX}${key}`,
                JSON.stringify({
                    createdAt: Date.now(),
                    payload
                })
            );

            if (!stored) {
                return window.parker.route('pages.search', {}, rawQuery);
            }

            return window.parker.route('pages.search', {}, `state_key=${encodeURIComponent(key)}`);
        },

        load(key) {
            if (!key) return null;

            this.cleanup();

            const raw = safeSessionStorage.get(`${SEARCH_HANDOFF_PREFIX}${key}`);
            if (!raw) return null;

            try {
                const parsed = JSON.parse(raw);
                return parsed.payload || null;
            } catch (e) {
                safeSessionStorage.remove(`${SEARCH_HANDOFF_PREFIX}${key}`);
                return null;
            }
        }
    };

    const storyArcReaderUrl = (arc, contextScope, contextScopeId) => {
        if (!arc || !arc.first_issue_id) {
            return '#';
        }

        const params = new URLSearchParams({
            context_type: 'story_arc',
            story_arc: arc.name || '',
            context_scope: contextScope,
            context_scope_id: contextScopeId
        });

        return window.parker.readerRoute(arc.first_issue_id, params.toString());
    };


    // Export utilities
    window.parker = { ...(window.parker || {}),
        showToast,
        debounce,
        formatFileSize,
        formatBytes,
        formatDate,
        paginationMixin,
        storage,
        prefs,
        searchHandoff,
        storyArcReaderUrl
    };

})();
