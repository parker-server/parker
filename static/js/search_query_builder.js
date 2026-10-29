document.addEventListener('alpine:init', () => {
    Alpine.data('tagInput', (rule) => ({
        input: '',
        tags: [],
        suggestions: [],

        init() {
            this.syncTagsFromRule();
            this.$watch(() => rule.value, () => this.syncTagsFromRule());
        },

        syncTagsFromRule() {
            if (!rule.value) {
                this.tags = [];
                return;
            }

            const newTags = Array.isArray(rule.value) ? rule.value : [rule.value];
            if (JSON.stringify(newTags) !== JSON.stringify(this.tags)) {
                this.tags = [...newTags];
            }
        },

        async fetchSuggestions() {
            if (this.input.length < 2) {
                this.suggestions = [];
                return;
            }
            try {
                const res = await fetch(window.parker.route(
                    'search.suggestions',
                    {},
                    `field=${rule.field}&query=${this.input}`,
                ));
                this.suggestions = await res.json();
            } catch (error) {
                console.error(error);
            }
        },

        addTag() {
            if (!this.input.trim()) return;
            this.tags.push(this.input.trim());
            this.updateRuleValue();
            this.input = '';
            this.suggestions = [];
        },

        selectSuggestion(value) {
            this.tags.push(value);
            this.updateRuleValue();
            this.input = '';
            this.suggestions = [];
        },

        removeTag(index) {
            this.tags.splice(index, 1);
            this.updateRuleValue();
        },

        handleBackspace() {
            if (this.input === '' && this.tags.length > 0) {
                this.tags.pop();
                this.updateRuleValue();
            }
        },

        updateRuleValue() {
            rule.value = [...this.tags];
        },
    }));

    Alpine.data('searchBuilder', () => ({
        rules: [
            { id: 1, field: 'series', operator: 'contains', value: [] },
        ],
        match: 'all',
        limit: 50,
        sortBy: 'created',
        sortOrder: 'desc',
        resultMode: 'issues',
        // Mode of the results on screen, so cards never render the other mode's data
        resultsMode: 'issues',
        results: [],
        totalResults: 0,
        loading: false,
        hasSearched: false,
        showValidationErrors: false,
        nextId: 2,
        contextLibraryId: null,
        contextLibraryName: null,
        // Smart list being edited, kept in the address so saving after Back still updates it
        syncedSmartListId: null,

        init() {
            this.checkUrlParams();
        },

        // On the Advanced Search page, keep the address bar matching the last search,
        // so Back from a result reruns it and the address can be bookmarked or shared.
        // Searches too long for an address are stored in the session behind a short state_key.
        syncUrl(payload) {
            if (!('syncUrl' in this.$root.dataset) || !window.parker.searchHandoff) return;

            const url = window.parker.searchHandoff.buildUrl({
                match: payload.match,
                sortBy: payload.sort_by,
                sortOrder: payload.sort_order,
                limit: payload.limit,
                libraryId: payload.context_library_id,
                filters: payload.filters,
                resultMode: payload.result_mode,
                smartListId: this.syncedSmartListId,
            });
            window.history.replaceState(window.history.state, '', url);
        },

        applyHydratedFilters(filters) {
            if (!Array.isArray(filters) || filters.length === 0) return false;

            this.rules = filters.map((filter, index) => ({
                id: Date.now() + index + Math.random(),
                field: filter.field,
                operator: this.normalizeRuleOperator(filter.field, filter.operator),
                value: this.normalizeRuleValue(filter.field, filter.value),
            }));
            return true;
        },

        checkUrlParams() {
            const params = new URLSearchParams(window.location.search);
            let shouldSearch = false;

            if (params.has('sort_by')) {
                this.sortBy = params.get('sort_by');
                shouldSearch = true;
            }

            if (params.has('sort_order')) {
                this.sortOrder = params.get('sort_order');
            }

            if (params.get('result_mode') === 'series') {
                this.setResultMode('series', false);
            }

            // Keep it before the first search rewrites the address
            const smartListId = parseInt(params.get('smart_list_id'), 10);
            if (!Number.isNaN(smartListId)) this.syncedSmartListId = smartListId;

            if (params.has('state_key') && window.parker.searchHandoff) {
                const hydrated = window.parker.searchHandoff.load(params.get('state_key'));
                if (hydrated) {
                    if (hydrated.match) this.match = hydrated.match;
                    if (hydrated.sortBy) {
                        this.sortBy = hydrated.sortBy;
                        shouldSearch = true;
                    }
                    if (hydrated.sortOrder) this.sortOrder = hydrated.sortOrder;
                    if (hydrated.resultMode) this.setResultMode(hydrated.resultMode, false);
                    if (hydrated.limit) {
                        const hydratedLimit = parseInt(hydrated.limit, 10);
                        if (!Number.isNaN(hydratedLimit) && hydratedLimit > 0) {
                            this.limit = hydratedLimit;
                        }
                    }
                    if (hydrated.libraryId) {
                        this.contextLibraryId = parseInt(hydrated.libraryId, 10);
                        if (!Number.isNaN(this.contextLibraryId)) {
                            this.fetchLibraryName(this.contextLibraryId);
                            shouldSearch = true;
                        }
                    }
                    if (this.applyHydratedFilters(hydrated.filters)) shouldSearch = true;
                }
            }

            if (params.has('match')) this.match = params.get('match');
            if (params.has('limit')) {
                const urlLimit = parseInt(params.get('limit'), 10);
                if (!Number.isNaN(urlLimit) && urlLimit > 0) this.limit = urlLimit;
            }
            if (params.has('filters')) {
                try {
                    const parsedFilters = JSON.parse(params.get('filters'));
                    if (this.applyHydratedFilters(parsedFilters)) shouldSearch = true;
                } catch (error) {
                    console.error('Failed to parse search filters from URL', error);
                }
            }

            if (!params.has('filters') && params.has('field') && (params.has('value') || params.has('operator'))) {
                const field = params.get('field');
                this.rules = [{
                    id: Date.now(),
                    field,
                    operator: this.normalizeRuleOperator(field, params.get('operator') || 'contains'),
                    value: this.normalizeRuleValue(field, params.get('value')),
                }];
                shouldSearch = true;
            }

            if (params.has('library_id')) {
                this.contextLibraryId = parseInt(params.get('library_id'), 10);
                this.fetchLibraryName(this.contextLibraryId);
                shouldSearch = true;
            }

            if (shouldSearch) this.$nextTick(() => this.performSearch());
        },

        addRule() {
            this.rules.push({ id: this.nextId++, field: 'character', operator: 'contains', value: [] });
        },

        usesSimpleInput(field) {
            return ['title', 'year', 'summary', 'web', 'rating', 'parker_rating'].includes(field);
        },

        usesNumericInput(field) {
            return ['year', 'rating', 'parker_rating'].includes(field);
        },

        numericInputStep(field) {
            return field === 'year' ? 1 : 0.1;
        },

        numericInputMin() {
            return 0;
        },

        numericInputMax(field) {
            return field === 'year' ? undefined : 5;
        },

        normalizeRuleValue(field, value) {
            if (this.usesSimpleInput(field)) {
                return Array.isArray(value) ? value[0] ?? '' : value ?? '';
            }
            if (Array.isArray(value)) return value;
            if (value === null || value === undefined || value === '') return [];
            return [value];
        },

        operatorOptions(field) {
            if (['rating', 'parker_rating', 'year'].includes(field)) {
                return [
                    { value: 'equal', label: 'Equals' },
                    { value: 'not_equal', label: 'Not Equals' },
                    { value: 'at_least', label: 'At Least' },
                    { value: 'at_most', label: 'At Most' },
                    { value: 'is_empty', label: 'Is Empty' },
                    { value: 'is_not_empty', label: 'Is Not Empty' },
                ];
            }
            if (['series', 'library'].includes(field)) {
                return [
                    { value: 'equal', label: 'Equals' },
                    { value: 'not_equal', label: 'Not Equals' },
                    { value: 'contains', label: 'Contains' },
                    { value: 'does_not_contain', label: 'Does Not Contain' },
                ];
            }
            if (['publisher', 'imprint', 'format', 'age_rating', 'language'].includes(field)) {
                return [
                    { value: 'equal', label: 'Equals' },
                    { value: 'not_equal', label: 'Not Equals' },
                    { value: 'contains', label: 'Contains' },
                    { value: 'does_not_contain', label: 'Does Not Contain' },
                    { value: 'is_empty', label: 'Is Empty' },
                    { value: 'is_not_empty', label: 'Is Not Empty' },
                ];
            }
            if (['title', 'summary', 'web'].includes(field)) {
                return [
                    { value: 'equal', label: 'Equals' },
                    { value: 'not_equal', label: 'Not Equals' },
                    { value: 'contains', label: 'Contains' },
                    { value: 'does_not_contain', label: 'Does Not Contain' },
                    { value: 'must_contain', label: 'Must Contain (All)' },
                    { value: 'is_empty', label: 'Is Empty' },
                    { value: 'is_not_empty', label: 'Is Not Empty' },
                ];
            }
            return [
                { value: 'equal', label: 'Equals' },
                { value: 'not_equal', label: 'Not Equals' },
                { value: 'contains', label: 'Contains (Any)' },
                { value: 'does_not_contain', label: 'Does Not Contain' },
                { value: 'must_contain', label: 'Must Contain (All)' },
                { value: 'is_empty', label: 'Is Empty' },
                { value: 'is_not_empty', label: 'Is Not Empty' },
            ];
        },

        defaultOperatorForField(field) {
            const options = this.operatorOptions(field);
            return options.find(option => option.value === 'contains')?.value || options[0].value;
        },

        normalizeRuleOperator(field, operator) {
            const options = this.operatorOptions(field);
            return options.some(option => option.value === operator)
                ? operator
                : this.defaultOperatorForField(field);
        },

        removeRule(index) {
            if (this.rules.length === 1) {
                this.rules[0].value = [];
                return;
            }
            this.rules.splice(index, 1);
        },

        resetRule(rule) {
            rule.value = [];
            rule.operator = this.normalizeRuleOperator(rule.field, rule.operator);
        },

        ruleRequiresValue(rule) {
            return !['is_empty', 'is_not_empty'].includes(rule.operator);
        },

        ruleHasValue(rule) {
            if (!this.ruleRequiresValue(rule)) return true;
            if (Array.isArray(rule.value)) return rule.value.length > 0;
            if (typeof rule.value === 'number') return !Number.isNaN(rule.value);
            if (typeof rule.value === 'string') return rule.value.trim() !== '';
            return rule.value !== null && rule.value !== undefined;
        },

        ruleValidationMessage(rule) {
            return !this.ruleRequiresValue(rule) || this.ruleHasValue(rule)
                ? ''
                : 'Enter a value or choose "Is Empty".';
        },

        ruleHasValidationError(rule) {
            return this.showValidationErrors && this.ruleValidationMessage(rule) !== '';
        },

        hasValidationErrors() {
            return this.rules.some(rule => this.ruleValidationMessage(rule) !== '');
        },

        ensureValidRules() {
            if (!this.hasValidationErrors()) {
                this.showValidationErrors = false;
                return true;
            }
            this.showValidationErrors = true;
            window.parker.showToast('Enter a value for each rule or choose "Is Empty".', 'warning');
            return false;
        },

        defaultSortOrder(sortBy) {
            return ['series', 'title'].includes(sortBy) ? 'asc' : 'desc';
        },

        handleSortByChange() {
            this.sortOrder = this.defaultSortOrder(this.sortBy);
        },

        // Issue Title and Page Count have no series-level meaning
        sortOptionAvailable(value) {
            return this.resultMode !== 'series' || !['title', 'page_count'].includes(value);
        },

        setResultMode(mode, rerun = true) {
            this.resultMode = mode === 'series' ? 'series' : 'issues';
            if (!this.sortOptionAvailable(this.sortBy)) {
                this.sortBy = 'series';
                this.handleSortByChange();
            }
            if (rerun && this.hasSearched) this.performSearch();
        },

        get resultsCountLabel() {
            return this.resultsMode === 'series' ? `${this.totalResults} series found` : `${this.totalResults} found`;
        },

        sortOrderLabel(order) {
            if (['series', 'title'].includes(this.sortBy)) return order === 'asc' ? 'A to Z' : 'Z to A';
            if (['created', 'updated', 'year'].includes(this.sortBy)) return order === 'desc' ? 'Newest First' : 'Oldest First';
            if (this.sortBy === 'page_count') return order === 'desc' ? 'Most Pages' : 'Fewest Pages';
            if (['rating', 'parker_rating'].includes(this.sortBy)) return order === 'desc' ? 'Highest First' : 'Lowest First';
            return order === 'asc' ? 'Ascending' : 'Descending';
        },

        async performSearch() {
            if (!this.ensureValidRules()) return;
            this.loading = true;
            this.hasSearched = true;
            this.results = [];
            const mode = this.resultMode;
            this.resultsMode = mode;
            const payload = this.buildSearchPayload();
            this.syncUrl(payload);
            try {
                const res = await fetch(window.parker.route(mode === 'series' ? 'comics.search_series' : 'comics.search'), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload),
                });
                if (!res.ok) throw new Error('Search failed');
                const data = await res.json();
                // Ignore a response for a mode the user has since switched away from
                if (mode !== this.resultMode) return;
                this.results = data.results;
                this.totalResults = data.total;
            } catch (error) {
                console.error('Search failed', error);
            } finally {
                this.loading = false;
            }
        },

        buildSearchPayload() {
            return {
                match: this.match,
                filters: this.rules.map(rule => ({
                    field: rule.field,
                    operator: rule.operator,
                    value: Array.isArray(rule.value) && rule.value.length === 0 ? null : rule.value,
                })).filter(rule => {
                    if (['is_empty', 'is_not_empty'].includes(rule.operator)) return true;
                    if (rule.value === null || rule.value === undefined) return false;
                    if (typeof rule.value === 'string' && rule.value.trim() === '') return false;
                    return !Array.isArray(rule.value) || rule.value.length > 0;
                }),
                sort_by: this.sortBy,
                sort_order: this.sortOrder,
                limit: parseInt(this.limit, 10),
                context_library_id: this.contextLibraryId,
                result_mode: this.resultMode,
            };
        },

        resetLibraryContext() {
            this.contextLibraryId = null;
            this.contextLibraryName = null;
            this.performSearch();
        },

        async fetchLibraryName(id) {
            try {
                const res = await fetch(window.parker.route('libraries.detail', { library_id: id }));
                if (res.ok) this.contextLibraryName = (await res.json()).name;
            } catch (error) {
                console.error(error);
            }
        },
    }));
});
