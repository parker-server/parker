document.addEventListener('alpine:init', () => {
    Alpine.data('advancedSearchPage', () => ({
        builder: null,
        savedSearches: [],
        smartLists: [],
        saveModalOpen: false,
        newSearchName: '',
        loadedListId: null,

        async init() {
            await this.$nextTick();
            this.builder = Alpine.$data(this.$root.querySelector('[data-search-builder]'));
            await this.loadSavedList();
            this.loadSmartListFromUrl();
        },

        async loadSavedList() {
            try {
                const [savedRes, smartRes] = await Promise.all([
                    fetch(window.parker.route('saved_searches.list')),
                    fetch(window.parker.route('smart_lists.list')),
                ]);
                if (savedRes.ok) this.savedSearches = await savedRes.json();
                if (smartRes.ok) {
                    const rawSmartLists = await smartRes.json();
                    this.smartLists = rawSmartLists.map(list => ({
                        id: list.id,
                        name: list.name,
                        icon: list.icon,
                        query: list.query,
                        isSmart: true,
                    }));
                }
            } catch (error) {
                console.error(error);
            }
        },

        loadSmartListFromUrl() {
            const id = Number(new URLSearchParams(window.location.search).get('smart_list_id'));
            if (!Number.isInteger(id)) return;

            const list = this.smartLists.find(item => item.id === id);
            if (!list) return;

            // An address written after a search already holds the (possibly edited) search,
            // which the builder has run; just remember which smart list is being edited.
            const params = new URLSearchParams(window.location.search);
            if (params.has('sort_by') || params.has('state_key')) {
                this.loadedListId = list.id;
                this.newSearchName = list.name;
                this.builder.syncedSmartListId = list.id;
                return;
            }
            this.applySearch(list);
        },

        openSaveModal() {
            this.saveModalOpen = true;
        },

        async saveSearch() {
            if (!this.newSearchName.trim() || !this.builder?.ensureValidRules()) return;
            try {
                const res = await fetch(window.parker.route('saved_searches.create'), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        name: this.newSearchName,
                        query: this.builder.buildSearchPayload(),
                    }),
                });
                if (!res.ok) throw new Error('Could not save search configuration');

                this.saveModalOpen = false;
                this.newSearchName = '';
                this.loadedListId = null;
                await this.loadSavedList();
                window.parker.showToast('Search configuration saved!', 'success');
            } catch (error) {
                console.error(error);
                window.parker.showToast?.(error.message || 'Could not save search configuration.', 'error');
            }
        },

        async deleteSearch(id) {
            const confirmed = await window.parker.dialog.confirm(
                'Delete Item?',
                'Delete this saved search?',
                {isDestructive: true, confirmText: 'Delete'},
            );
            if (!confirmed) return;

            await fetch(window.parker.route('saved_searches.delete', {search_id: id}), {method: 'DELETE'});
            await this.loadSavedList();
            window.parker.showToast('Saved Search deleted', 'success');
        },

        applySearch(item) {
            const query = item.query;
            this.loadedListId = item.id;
            this.builder.syncedSmartListId = item.isSmart ? item.id : null;
            this.builder.match = query.match || 'all';
            this.builder.sortBy = query.sort_by || 'created';
            this.builder.sortOrder = query.sort_order || 'desc';
            this.builder.limit = query.limit || 50;
            this.builder.setResultMode(query.result_mode || 'issues', false);
            if (!this.builder.applyHydratedFilters(query.filters || [])) {
                this.builder.rules = [
                    {id: Date.now(), field: 'series', operator: 'contains', value: []},
                ];
            }

            this.$nextTick(() => {
                this.builder.performSearch();
                this.newSearchName = item.name;
            });
        },

        async saveAsSmartList() {
            if (!this.newSearchName.trim()) {
                await window.parker.dialog.alert('Error', 'Please enter a name first.');
                return;
            }
            if (!this.builder?.ensureValidRules()) return;

            const isNewList = this.loadedListId === null;
            try {
                const res = await fetch(
                    isNewList
                        ? window.parker.route('smart_lists.create')
                        : window.parker.route('smart_lists.update', {list_id: this.loadedListId}),
                    {
                        method: isNewList ? 'POST' : 'PATCH',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            name: this.newSearchName,
                            query: this.builder.buildSearchPayload(),
                        }),
                    },
                );
                if (!res.ok) throw new Error('Could not save smart list');

                this.saveModalOpen = false;
                if (isNewList) this.newSearchName = '';
                window.parker.showToast(isNewList ? 'Smart List created!' : 'Smart List updated!', 'success');
                await this.loadSavedList();
            } catch (error) {
                console.error(error);
                window.parker.showToast?.(error.message || 'Could not save smart list.', 'error');
            }
        },
    }));
});
