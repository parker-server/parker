(() => {
    const STORAGE_PREFIX = 'parker.';
    const LEGACY_KEY_ALIASES = Object.freeze({
        'fx.enabled': ['parker-fx-enabled']
    });

    const normalizeStorageKey = (key) => {
        const rawKey = String(key);
        return rawKey.startsWith(STORAGE_PREFIX) ? rawKey : `${STORAGE_PREFIX}${rawKey}`;
    };

    const legacyStorageKeys = (key) => {
        const rawKey = String(key);
        const normalizedKey = normalizeStorageKey(rawKey);
        const aliases = LEGACY_KEY_ALIASES[rawKey] || [];
        const keys = rawKey === normalizedKey ? [] : [rawKey];
        return [...new Set([...keys, ...aliases])].filter((candidate) => candidate !== normalizedKey);
    };

    const storage = {
        prefix: STORAGE_PREFIX,
        key(key) {
            return normalizeStorageKey(key);
        },
        getString(key, defaultValue = null) {
            const storageKey = normalizeStorageKey(key);

            try {
                const item = localStorage.getItem(storageKey);
                if (item !== null) {
                    legacyStorageKeys(key).forEach((legacyKey) => localStorage.removeItem(legacyKey));
                    return item;
                }

                for (const legacyKey of legacyStorageKeys(key)) {
                    const legacyItem = localStorage.getItem(legacyKey);
                    if (legacyItem !== null) {
                        localStorage.setItem(storageKey, legacyItem);
                        localStorage.removeItem(legacyKey);
                        return legacyItem;
                    }
                }

                return defaultValue;
            } catch (e) {
                console.error('Error reading from localStorage:', e);
                return defaultValue;
            }
        },
        setString(key, value) {
            const storageKey = normalizeStorageKey(key);

            try {
                localStorage.setItem(storageKey, String(value));
                legacyStorageKeys(key).forEach((legacyKey) => localStorage.removeItem(legacyKey));
            } catch (e) {
                console.error('Error writing to localStorage:', e);
            }
        },
        get(key, defaultValue = null) {
            const item = this.getString(key, null);
            if (item === null) {
                return defaultValue;
            }

            try {
                return JSON.parse(item);
            } catch (e) {
                console.error('Error parsing localStorage value:', e);
                return defaultValue;
            }
        },
        set(key, value) {
            this.setString(key, JSON.stringify(value));
        },
        remove(key) {
            const storageKey = normalizeStorageKey(key);

            try {
                localStorage.removeItem(storageKey);
                legacyStorageKeys(key).forEach((legacyKey) => localStorage.removeItem(legacyKey));
            } catch (e) {
                console.error('Error removing from localStorage:', e);
            }
        },
        has(key) {
            return this.getString(key, null) !== null;
        }
    };

    window.parker = { ...(window.parker || {}), storage };
})();
