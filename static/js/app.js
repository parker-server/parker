/**
 * 1. GLOBAL FETCH WRAPPER (For Alpine.js & Custom JS)
 * Automatically injects JWT token and handles 401 Redirects.
 */
(function() {
    const originalFetch = window.fetch;

    window.fetch = async function(url, options = {}) {
        options.headers = options.headers || {};

        // Inject Token
        const token = localStorage.getItem('token');
        if (token) {
            if (options.headers instanceof Headers) {
                options.headers.append('Authorization', `Bearer ${token}`);
            } else {
                options.headers['Authorization'] = `Bearer ${token}`;
            }
        }

        try {
            const response = await originalFetch(url, options);

            // Handle Unauthorized (401)
            if (response.status === 401) {
                console.warn('Session expired. Redirecting to login.');
                localStorage.removeItem('token');

                // Avoid infinite reload loops if already on login
                if (!window.location.pathname.includes('/login')) {
                    window.location.href = '/login';
                }
            }

            return response;
        } catch (error) {
            console.error('Network Error:', error);
            throw error;
        }
    };
})();

// Toast notifications
function showToast(message, type = 'info') {
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

// 2. HTMX EVENT HANDLERS
// Inject Token into HTMX requests
document.body.addEventListener('htmx:configRequest', function(evt) {
    const token = localStorage.getItem('token');
    if (token) {
        evt.detail.headers['Authorization'] = `Bearer ${token}`;
    }
});

document.body.addEventListener('htmx:beforeRequest', function(evt) {
    // console.log('Request starting:', evt.detail);
});

document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (!evt.detail.successful) {
        // console.error('Request failed:', evt.detail);
        // Don't show toast for 401s, the responseError handler will handle redirect
        if (evt.detail.xhr.status !== 401) {
            showToast('Request failed. Please try again.', 'error');
        }
    }
});

document.body.addEventListener('htmx:responseError', function(evt) {
    // Handle HTMX 401 Redirects
    if (evt.detail.xhr.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/login';
        return;
    }
    console.error('Response error:', evt.detail);
    showToast('Server error. Please try again later.', 'error');
});

// Handle JSON responses in HTMX
document.body.addEventListener('htmx:beforeSwap', function(evt) {
    // Check if response is JSON
    const contentType = evt.detail.xhr.getResponseHeader('Content-Type');
    if (contentType && contentType.includes('application/json')) {
        try {
            const data = JSON.parse(evt.detail.xhr.response);
            if (data.message) showToast(data.message, 'success');
            if (data.error) showToast(data.error, 'error');
        } catch (e) {
            console.error('Error parsing JSON:', e);
        }
    }
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

// Debounce utility
function debounce(func, wait) {
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
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

// Format date
function formatDate(dateString) {
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
const storage = {
    get(key, defaultValue = null) {
        try {
            const item = localStorage.getItem(key);
            return item ? JSON.parse(item) : defaultValue;
        } catch (e) {
            console.error('Error reading from localStorage:', e);
            return defaultValue;
        }
    },
    set(key, value) {
        try {
            localStorage.setItem(key, JSON.stringify(value));
        } catch (e) { console.error('Error writing to localStorage:', e); }
    },
    remove(key) {
        try { localStorage.removeItem(key); } catch (e) { }
    }
};

const prefs = {
    getViewMode() { return storage.get('viewMode', 'grid'); },
    setViewMode(mode) { storage.set('viewMode', mode); },
    getFitMode() { return storage.get('fitMode', 'contain'); },
    setFitMode(mode) { storage.set('fitMode', mode); }
};

// Initialization
document.addEventListener('DOMContentLoaded', function() {
    // Restore user preferences
    const fitMode = prefs.getFitMode();
    const fitModeSelect = document.getElementById('fit-mode');
    if (fitModeSelect) {
        fitModeSelect.value = fitMode;
    }

    // Active Nav State
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('text-blue-400');
        }
    });
});

// Export utilities
window.comicServer = {
    showToast,
    debounce,
    formatFileSize,
    formatDate,
    storage,
    prefs
};