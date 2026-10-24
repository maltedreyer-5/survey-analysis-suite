// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
/**
 * app.js - Alpine.js application of the survey dashboard
 * 
 * Main component with:
 * - data loading (dashboard_data.json, themes.json)
 * - filtering by the segmentation dimensions of the survey
 * - theme navigation
 * - chart rendering
 * - multilingual output
 */

function dashboard() {
    return {
        // ============================================
        // STATE
        // ============================================
        
        // data from the JSON files
        data: {},
        themes: {},
        correlationsData: null,
        
        // UI-State
        lang: 'de',
        filter: { 
            type: 'all',  // 'all' or a segment dimension id from data.segments
            value: 'all' 
        },
        showSpecificClusters: false,  // free text: segment-specific instead of global clusters
        activeTheme: null,
        
        // Status
        loading: true,
        error: null,
        sidebarOpen: false,
        sidebarCollapsed: false, // desktop: sidebar collapsed
        
        // Navigation Mode: 'home' | 'summary' | 'questions' | 'themes' | 'keywords' | 'correlations' | 'segmentcomparison' | 'myprofile'
        viewMode: 'themes',
        
        // Survey Structure
        surveyStructure: null,
        
        // accordion state of the cluster details
        openClusters: {},        // { questionId: true/false }
        openSingleClusters: {},  // { "questionId-clusterIdx": true/false }
        
        // Small Multiples State
        openSmallMultiples: {},  // { questionId: true/false }
        
        // "own selection" filter (session only) - multi-select arrays
        // Selected values per dimension id; keys are added by initDynamicFilters()
        myProfile: {
            active: false
        },
        
        // applied filters (updated only on the button click)
        appliedFilters: {
            n: 0  // computed n; dimension ids are added by initDynamicFilters()
        },
        
        // Keyword Search
        keywordSearch: '',
        selectedKeyword: null,
        
        // Correlations View
        correlationView: 'cards',  // 'cards' | 'heatmap' | 'network'
        correlationStrengthFilter: 'all',  // 'all' | 'strong' | 'medium' | 'weak'
        
        // segment comparison view
        segmentComparisonThreshold: 10,  // deviation threshold in %
        segmentSubView: 'clusters',  // 'clusters' | 'deviations': sub-navigation of the segment comparison
        openSegmentClusterQuestions: {},  // accordion state of the questions in the cluster view { questionId: true/false }
        openSegmentDeviationValues: {}, // accordion state of the segment values in the deviations view { key: true/false }
        
        // data of the extended segment comparison
        segmentClustersData: null,   // segment_clusters.json (per dimension)
        comparisonDimension: null,   // dimension shown in the segment comparison view
        segmentInsightsData: null,   // segment_insights.json
        
        // hash navigation: prevents double updates
        _hashUpdateInProgress: false,
        
        // ============================================
        // COMPUTED PROPERTIES (as getters)
        // ============================================
        
        /**
         * Current sample size based on the filter
         */
        /**
         * Current (filtered) sample size, as a method for the Alpine.js reactivity
         * The parameters are passed only for the Alpine.js reactivity
         */
        getCurrentN() {
            // "own selection" view: use appliedFilters.n (computed already)
            if (this.viewMode === 'myprofile') {
                return this.appliedFilters.n || 0;
            }
            
            if (this.filter.type === 'all' || this.filter.value === 'all') {
                return this.data.metadata?.total_responses || 0;
            }
            
            // compute n from the first available question with segment data
            const questions = this.data.questions || {};
            for (const qid of Object.keys(questions)) {
                const q = questions[qid];
                const segmentKey = `by_${this.filter.type}`;
                const segmentData = q.results?.[segmentKey]?.[this.filter.value];
                if (segmentData && typeof segmentData.n === 'number') {
                    return segmentData.n;
                }
            }
            
            return this.data.metadata?.total_responses || 0;
        },
        
        // short form for the templates (x-text="currentN")
        get currentN() {
            return this.getCurrentN();
        },
        
        /**
         * Total sample size
         */
        get totalN() {
            return this.data.metadata?.total_responses || 0;
        },
        
        /**
         * Whether the current n counts as low
         */
        get isLowN() {
            return this.currentN < this.data?.metadata?.warning_n && this.currentN >= this.minN();
        },
        
        /**
         * Whether n is below the minimum
         */
        get isTooLowN() {
            return this.currentN < this.minN();
        },
        
        /**
         * Survey year from the metadata
         */
        get surveyYear() {
            return this.data.metadata?.survey_year || new Date().getFullYear();
        },
        
        /**
         * Sorted theme list for the navigation
         * A method instead of a getter for a better Alpine.js reactivity
         */
        getThemeList() {
            if (!this.themes || !this.themes.themes) return [];
            return Object.entries(this.themes.themes)
                .map(([id, theme]) => ({
                    id,
                    ...theme
                }))
                .sort((a, b) => (a.order || 999) - (b.order || 999));
        },
        
        // ============================================
        // LIFECYCLE
        // ============================================
        
        /**
         * Initialisation: loads the data and renders initially
         */
        async init() {
            this.loading = true;
            this.error = null;
            
            // language from the URL or the browser setting
            const urlParams = new URLSearchParams(window.location.search);
            const urlLang = urlParams.get('lang');
            // Languages with interface texts (i18n.js); the export's languages narrow this after loading
            const uiLangs = Object.keys(window.i18n || {});
            if (urlLang && uiLangs.includes(urlLang)) {
                this.lang = urlLang;
            } else {
                const browserLang = navigator.language?.substring(0, 2);
                if (uiLangs.includes(browserLang)) {
                    this.lang = browserLang;
                }
            }
            
            try {
                // primary: load the JSON files from the data/ folder (server operation)
                console.log('Versuche JSON-Dateien zu laden...');
                
                let dataLoaded = false;
                
                try {
                    const [dataRes, themesRes, structureRes, correlationsRes, segmentClustersRes, segmentInsightsRes] = await Promise.all([
                        fetch('data/dashboard_data.json'),
                        fetch('data/themes.json'),
                        fetch('data/survey_structure.json').catch(() => null),
                        fetch('data/correlations.json').catch(() => null),
                        fetch('data/segment_clusters.json').catch(() => null),
                        fetch('data/segment_insights.json').catch(() => null)
                    ]);
                    
                    if (dataRes.ok && themesRes.ok) {
                        this.data = await dataRes.json();
                        this.themes = await themesRes.json();
                        this.surveyStructure = structureRes?.ok ? await structureRes.json() : null;
                        this.correlationsData = correlationsRes?.ok ? await correlationsRes.json() : null;
                        this.segmentClustersData = segmentClustersRes?.ok ? await segmentClustersRes.json() : null;
                        this.segmentInsightsData = segmentInsightsRes?.ok ? await segmentInsightsRes.json() : null;
                        dataLoaded = true;
                        this.initDynamicFilters();
                        this.minN();  // publish the threshold for charts.js
                        this.initSurveyTitle();
                        console.log('✅ Daten aus JSON-Dateien geladen');
                        if (this.segmentClustersData) console.log('  ✅ segment_clusters.json geladen');
                        if (this.segmentInsightsData) console.log('  ✅ segment_insights.json geladen');
                    }
                } catch (fetchError) {
                    console.log('JSON fetch fehlgeschlagen (evtl. lokaler file:// Zugriff)');
                }
                
                // fallback: embedded JS data for offline/local use
                if (!dataLoaded) {
                    if (typeof DASHBOARD_DATA !== 'undefined' && typeof THEMES_DATA !== 'undefined') {
                        this.data = DASHBOARD_DATA;
                        this.themes = THEMES_DATA;
                        this.surveyStructure = typeof SURVEY_STRUCTURE !== 'undefined' ? SURVEY_STRUCTURE : null;
                        this.correlationsData = typeof CORRELATIONS_DATA !== 'undefined' ? CORRELATIONS_DATA : null;
                        this.segmentClustersData = typeof SEGMENT_CLUSTERS_DATA !== 'undefined' ? SEGMENT_CLUSTERS_DATA : null;
                        this.segmentInsightsData = typeof SEGMENT_INSIGHTS_DATA !== 'undefined' ? SEGMENT_INSIGHTS_DATA : null;
                        this.initDynamicFilters();
                        this.minN();  // publish the threshold for charts.js
                        this.initSurveyTitle();
                        console.log('✅ Daten aus eingebetteten JS-Dateien geladen (Offline-Modus)');
                    } else {
                        throw new Error('No data available. For offline use, run build_offline.py.');
                    }
                }
                
                // make the first theme active (sorted by order)
                const themeIds = Object.keys(this.themes.themes || {});
                if (themeIds.length > 0) {
                    themeIds.sort((a, b) => {
                        const orderA = this.themes.themes[a]?.order || 999;
                        const orderB = this.themes.themes[b]?.order || 999;
                        return orderA - orderB;
                    });
                    this.activeTheme = themeIds[0];
                }
                
                // start view: themes only if the survey has themes (setViewMode)
                this.setViewMode(this.viewMode);
                this.loading = false;
                
                // initialise myProfileFilteredN
                this.myProfileFilteredN = this.data.metadata?.total_responses || 0;
                
                // render the charts after the DOM update (setTimeout for a safe DOM ready)
                setTimeout(() => {
                    this.renderAllCharts();
                }, 100);
                
                // watch the change of the correlation view
                this.$watch('correlationView', () => {
                    this.updateUrlHash();
                    if (this.viewMode === 'correlations') {
                        this.$nextTick(() => {
                            setTimeout(() => this.renderCorrelationCharts(), 100);
                        });
                    }
                });
                
                // watch the correlation strength filter
                this.$watch('correlationStrengthFilter', () => {
                    if (this.viewMode === 'correlations') {
                        this.$nextTick(() => {
                            setTimeout(() => this.renderCorrelationCharts(), 100);
                        });
                    }
                });
                
                // watch the theme change, so the charts are rendered again
                this.$watch('activeTheme', (newTheme, oldTheme) => {
                    if (this.viewMode === 'themes' && newTheme !== oldTheme) {
                        this.$nextTick(() => {
                            setTimeout(() => {
                                this.renderAllCharts();
                            }, 200);
                        });
                    }
                });
                
                // hash-based navigation: restore the state from the URL
                this.restoreStateFromHash();
                
                // support the browser back/forward buttons
                window.addEventListener('popstate', () => {
                    this._hashUpdateInProgress = true;
                    this.restoreStateFromHash();
                    this.$nextTick(() => {
                        setTimeout(() => {
                            this._hashUpdateInProgress = false;
                            if (this.viewMode === 'correlations') {
                                this.renderCorrelationCharts();
                            } else {
                                this.renderAllCharts();
                            }
                        }, 100);
                    });
                });
                
            } catch (err) {
                console.error('Fehler beim Laden:', err);
                this.error = err.message;
                this.loading = false;
            }
        },
        
        // ============================================
        // FILTER METHODS
        // ============================================
        
        /**
         * Available values of the current filter type
         */

        /**
         * The list of the available segment ids
         * @returns {string[]} e.g. ['role', 'area']
         */
        getAvailableSegmentIds() {
            if (!this.data?.segments) return [];
            return Object.keys(this.data.segments);
        },
        
        /**
         * The label of a segment in the current language
         * @param {string} segmentId - e.g. 'role'
         * @returns {string} e.g. 'Rolle' or 'Role'
         */
        getSegmentLabel(segmentId) {
            const segment = this.data?.segments?.[segmentId];
            if (!segment) return this.getSegmentLabelFallback(segmentId);
            // try label.<lang>, then labels.<lang>, then the id
            const labels = segment.label || segment.labels || {};
            return pickLang(labels, this.lang) || this.getSegmentLabelFallback(segmentId);
        },
        
        /**
         * Initialises the filter objects from the available segments
         * Called after the data has been loaded
         */
        initDynamicFilters() {
            // normalise the segment formats (spec appendix B: values as {label: count}, "labels" instead of "label")
            Object.values(this.data?.segments || {}).forEach(seg => {
                if (!seg || typeof seg !== 'object') return;
                if (!seg.label && seg.labels) seg.label = seg.labels;
                if (seg.values && !Array.isArray(seg.values) && typeof seg.values === 'object') {
                    seg.values = Object.entries(seg.values).map(([k, n]) => ({
                        key: k, label: { de: k, fr: k, en: k, it: k }, n: n
                    }));
                }
            });
            const segmentIds = this.getAvailableSegmentIds();
            
            // extend myProfile and appliedFilters dynamically
            segmentIds.forEach(segId => {
                if (!(segId in this.myProfile)) {
                    this.myProfile[segId] = [];
                }
                if (!(segId in this.appliedFilters)) {
                    this.appliedFilters[segId] = [];
                }
            });
        },

        /**
         * Loads the survey title from the metadata into the i18n structure
         */
        initSurveyTitle() {
            const meta = this.data?.metadata || {};
            const surveyTitle = meta.survey_title || {};
            const surveyYear = meta.survey_year || '';
            
            // Source language of the survey: fallback for texts missing in a language (pickLang)
            window.__surveySourceLanguage = meta.source_language || undefined;
            // The export's languages decide the switcher; keep the chosen one if offered
            const offered = this.availableLanguages();
            if (!offered.includes(this.lang)) this.lang = offered[0];
            const firstTitle = Object.values(surveyTitle).find(v => v) || '';
            // set the title for all languages
            Object.keys(window.i18n || {}).forEach(lang => {
                if (window.i18n && window.i18n[lang]) {
                    // without a configured title: the generic subtitle, never another survey's name
                    const title = surveyTitle[lang] || firstTitle || window.i18n[lang].subtitle || '';
                    window.i18n[lang].title = title;
                    window.i18n[lang].survey_title = title + (surveyYear ? ' ' + surveyYear : '');
                    window.i18n[lang].survey_subtitle = meta.survey_subtitle?.[lang]
                        || Object.values(meta.survey_subtitle || {}).find(v => v) || '';
                }
            });
            
            // Update document title
            const currentTitle = pickLang(surveyTitle, this.lang) || 'Umfrage';
            document.title = currentTitle + (surveyYear ? ' ' + surveyYear : '') + ' - Dashboard';
        },

        getFilterValues() {
            if (!this.data.segments || this.filter.type === 'all') {
                return [];
            }
            const values = this.data.segments[this.filter.type]?.values || [];
            // always return objects with key and label
            return values.map(v => {
                if (typeof v === 'object' && v.key) {
                    return v; // in the right format already
                }
                // convert a string into an object
                return { key: v, label: { de: v, fr: v, en: v, it: v } };
            });
        },
        
        /**
         * Called when the filter type changes
         */
        onFilterTypeChange() {
            this.filter.value = 'all';
            this.renderAllCharts();
        },
        
        /**
         * Called when the filter value changes
         */
        applyFilter() {
            this.renderAllCharts();
        },
        
        /**
         * Resets the filter
         */
        resetFilter() {
            this.filter.type = 'all';
            this.filter.value = 'all';
            this.renderAllCharts();
        },
        
        // ============================================
        // THEME METHODS
        // ============================================
        
        /**
         * Switches the theme and renders the charts again
         */
        setActiveTheme(themeId) {
            if (this.activeTheme === themeId) return;
            this.activeTheme = themeId;
            
            // update the URL hash
            this.updateUrlHash();
            
            // wait until Alpine.js has updated the DOM
            this.$nextTick(() => {
                setTimeout(() => {
                    this.renderAllCharts();
                }, 150);
            });
        },
        
        /**
         * Question ids of the active theme
         */
        getQuestionsForTheme() {
            if (!this.activeTheme || !this.themes.themes) {
                return [];
            }
            
            return this.themes.themes[this.activeTheme]?.questions || [];
        },
        
        /**
         * The active theme object
         */
        getActiveTheme() {
            if (!this.activeTheme || !this.themes.themes) {
                return null;
            }
            return this.themes.themes[this.activeTheme];
        },
        
        // ============================================
        // DATA METHODS
        // ============================================
        
        /**
         * Question object by id
         */
        getQuestion(qid) {
            return this.data.questions?.[qid] || null;
        },
        
        /**
         * Question text in the current language
         */
        getQuestionText(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.text) return qid;
            return pickLang(q.text, this.lang) || qid;
        },
        
        /**
         * Full question text (full_text) in the current language
         * Falls back to text without full_text
         * @param {string} qid - question id
         * @returns {string|null} full question text or null
         */
        getQuestionFullText(qid) {
            const q = this.getQuestion(qid);
            if (!q) return null;
            
            // is full_text present?
            if (q.full_text && typeof q.full_text === 'object') {
                const fullText = pickLang(q.full_text, this.lang) || q.full_text['en'];
                if (fullText) return fullText;
            }
            
            // fallback to text
            if (q.text && typeof q.text === 'object') {
                const text = pickLang(q.text, this.lang) || q.text['en'];
                if (text) return text;
            }
            
            return null;
        },
        
        /**
         * Whether a question has a full text (full_text) that differs
         * from the short text (text)
         * @param {string} qid - question id
         * @returns {boolean}
         */
        hasFullText(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.full_text) return false;
            
            const fullText = pickLang(q.full_text, this.lang) || q.full_text['en'];
            const shortText = pickLang(q.text, this.lang) || q.text?.['en'];
            
            // true only if full_text exists AND differs from text
            return fullText && fullText !== shortText;
        },
        
        /**
         * n of a specific question (filtered)
         * Uses appliedFilters in the profile view
         */
        getQuestionN(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.results) return 0;
            
            // "own selection" view: use appliedFilters
            if (this.viewMode === 'myprofile') {
                const filters = this.singleProfileFilters();
                const result = this.getCrossFilterData(q, filters);
                return result.data?.n || 0;
            }
            
            if (this.filter.type === 'all' || this.filter.value === 'all') {
                return q.results.all?.n || 0;
            }
            
            const segmentKey = `by_${this.filter.type}`;
            return q.results[segmentKey]?.[this.filter.value]?.n || 0;
        },
        
        /**
         * Total n of a question (without filters)
         */
        getQuestionTotalN(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.results) return 0;
            return q.results.all?.n || 0;
        },
        
        /**
         * Whether a question has enough data (n >= threshold)
         * Used to hide questions in the "own selection" view
         * Uses the same logic as renderChartByType
         */
        hasEnoughDataForQuestion(qid) {
            const q = this.getQuestion(qid);
            if (!q) return false;
            
            // free-text questions: the clusters are created separately and cannot be aggregated;
            // show them whenever clusters exist
            if (q.type === 'freetext') {
                // do clusters exist?
                if (q.clusters && q.clusters.length > 0) {
                    return true;
                }
                // fallback: do cluster_percentages exist?
                const allData = q.results?.all;
                if (allData?.cluster_percentages && Object.keys(allData.cluster_percentages).length > 0) {
                    return true;
                }
                return false;
            }
            
            // cooperation matrix: do values exist?
            if (q.type === 'cooperation_matrix') {
                const resultData = this.getQuestionData(qid);
                if (resultData?.values && Object.keys(resultData.values).length > 0) {
                    return (resultData.n || 0) >= 5;
                }
                return false;
            }
            
            // other question types: the default logic
            const resultData = this.getQuestionData(qid);
            if (!resultData) return false;
            
            // n may also be n_items or n_responses
            const n = resultData.n || resultData.n_items || resultData.n_responses || 0;
            return n >= this.minN();
        },
        
        /**
         * Whether a fallback is used for a question (not all filters applicable)
         * Uses appliedFilters
         */
        isUsingFallback(qid) {
            if (this.viewMode !== 'myprofile') return false;
            
            const q = this.getQuestion(qid);
            if (!q || !q.results) return false;
            
            // are any filters active at all?
            if (!this.isMyProfileFilterActive()) return false;
            
            const filters = this.singleProfileFilters();  // [{segment, value}]
            if (filters.length === 0) return false;
            
            const result = this.getCrossFilterData(q, filters);
            return result.isFallback;
        },
        
        /**
         * The filters actually used for a question
         */
        getUsedFilters(qid) {
            if (this.viewMode !== 'myprofile') return [];
            
            const q = this.getQuestion(qid);
            if (!q || !q.results) return [];
            
            const filters = this.getActiveMyProfileFilters();
            const result = this.getCrossFilterData(q, filters);
            return result.usedFilters;
        },
        
        /**
         * Whether a profile filter is active (based on the applied filters)
         * A filter is active if at least one array is not empty
         */
        isMyProfileFilterActive() {
            return this.getAvailableSegmentIds().some(id =>
                Array.isArray(this.appliedFilters[id]) && this.appliedFilters[id].length > 0);
        },
        
        /**
         * Whether the currently selected filters differ from the applied ones
         */
        hasUnappliedFilterChanges() {
            const arraysEqual = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);
            return this.getAvailableSegmentIds().some(id =>
                !arraysEqual(this.myProfile[id] || [], this.appliedFilters[id] || []));
        },
        
        /**
         * Toggles a value in a filter array
         */
        toggleFilterValue(filterType, value) {
            const arr = this.myProfile[filterType];
            const idx = arr.indexOf(value);
            if (idx === -1) {
                arr.push(value);
            } else {
                arr.splice(idx, 1);
            }
        },
        
        /**
         * Whether a value is selected in the filter
         */
        isFilterSelected(filterType, value) {
            return (this.myProfile[filterType] || []).includes(value);
        },
        
        /**
         * Selects or deselects all values of a filter
         */
        toggleAllFilterValues(filterType) {
            const allValues = this.getSegmentValues(filterType);
            if ((this.myProfile[filterType] || []).length === allValues.length) {
                // deselect all
                this.myProfile[filterType] = [];
            } else {
                // select all
                this.myProfile[filterType] = [...allValues];
            }
        },
        
        /**
         * Whether all values of a filter are selected
         */
        areAllFilterValuesSelected(filterType) {
            const allValues = this.getSegmentValues(filterType);
            return (this.myProfile[filterType] || []).length === allValues.length;
        },
        
        /**
         * A short label of the active filter
         * @returns {string} e.g. "Student + Staff, STEM"
         */
        getFilterLabel() {
            // One part per dimension: the value label if one value is selected,
            // otherwise "<count> <dimension>"
            const parts = [];
            for (const id of this.getAvailableSegmentIds()) {
                const selected = Array.isArray(this.appliedFilters[id]) ? this.appliedFilters[id] : [];
                if (selected.length === 1) {
                    parts.push(this.getSegmentValueLabel(id, selected[0], this.lang));
                } else if (selected.length > 1) {
                    parts.push(`${selected.length} × ${this.getSegmentLabel(id)}`);
                }
            }
            const defaultLabel = this.t('selection');
            return parts.length > 0 ? parts.join(', ') : defaultLabel;
        },
        
        /**
         * Whether cross filter data is available
         */
        supportsCrossFilters() {
            return this.data.metadata?.cross_filter_support === true;
        },
        
        /**
         * The applied profile filters
         * @returns {Object} e.g. {role: ['studium', 'lehre'], area: [], age_group: ['u25']}
         */
        getActiveMyProfileFilters() {
            return Object.fromEntries(this.getAvailableSegmentIds().map(id => [id, this.appliedFilters[id] || []]));
        },
        
        /**
         * All filter combinations of a multi-select
         * e.g. with role=['studium','lehre'] and area=['mint'] -> [{role:'studium',area:'mint'}, {role:'lehre',area:'mint'}]
         */
        getAllFilterCombinations() {
            // Cartesian product of the selected values of every configured dimension
            let combinations = [[]];
            for (const segment of this.getAvailableSegmentIds()) {
                const selected = Array.isArray(this.appliedFilters[segment]) ? this.appliedFilters[segment] : [];
                if (selected.length === 0) continue;
                combinations = combinations.flatMap(combo => selected.map(value => [...combo, { segment, value }]));
            }
            return combinations.filter(combo => combo.length > 0);
        },
        
        /** Profile filters with exactly one selected value per dimension, as [{segment, value}]. */
        singleProfileFilters() {
            const filters = [];
            for (const segment of this.getAvailableSegmentIds()) {
                const v = this.appliedFilters[segment];
                const value = Array.isArray(v) ? (v.length === 1 ? v[0] : null) : (v && v !== 'all' ? v : null);
                if (value) filters.push({ segment, value });
            }
            return filters;
        },
        
        /** True if a results object has a breakdown for any configured dimension. */
        hasAnySegmentResults(results) {
            return !!results && this.getAvailableSegmentIds().some(id => results[`by_${id}`]);
        },
        
        /** Source questions of the segment dimensions (not shown as content in the profile view). */
        segmentSourceQuestions() {
            return Object.values(this.data?.segments || {}).map(s => s.source_question).filter(Boolean);
        },
        
        /**
         * Builds the results key of a filter combination
         * @param {Array} filters - array of {segment, value} objects
         * @returns {string} e.g. "by_area_role"
         */
        buildCrossFilterKey(filters) {
            if (filters.length === 0) return 'all';
            if (filters.length === 1) return `by_${filters[0].segment}`;
            
            // sort the segments alphabetically and join them
            const segments = filters.map(f => f.segment).sort();
            return 'by_' + segments.join('_');
        },
        
        /**
         * Builds the value key of a filter combination
         * @param {Array} filters - array of {segment, value} objects
         * @returns {string} e.g. "mint|studium"
         */
        buildCrossFilterValueKey(filters) {
            if (filters.length === 0) return null;
            if (filters.length === 1) return filters[0].value;
            
            // join the values in the alphabetical order of the segments
            const sorted = [...filters].sort((a, b) => a.segment.localeCompare(b.segment));
            return sorted.map(f => f.value).join('|');
        },
        
        /**
         * Aggregates the data of several filter combinations
         * @param {Object} question - the question
         * @returns {Object} aggregated data {n, values, percentages, hasExactMatch}
         */
        getAggregatedFilterData(question) {
            if (!question || !question.results) {
                return null;
            }
            
            // no filters active -> all data
            if (!this.isMyProfileFilterActive()) {
                return question.results.all;
            }
            
            const combinations = this.getAllFilterCombinations();
            
            // collect all data
            let totalN = 0;
            const aggregatedValues = {};
            const seenKeys = new Set(); // avoids duplicates with fallbacks
            let hasAnyExactMatch = false;
            
            for (const combo of combinations) {
                const result = this.getCrossFilterData(question, combo);
                if (result.data && result.data.n) {
                    // exact match = NO fallback used
                    const isExactMatch = !result.isFallback;
                    
                    if (isExactMatch) {
                        hasAnyExactMatch = true;
                    }
                    
                    // a unique key for this data
                    const dataKey = this.buildCrossFilterKey(result.usedFilters) + '|' + 
                                   this.buildCrossFilterValueKey(result.usedFilters);
                    
                    // count n if:
                    // - exact match (no fallback)
                    // - OR only one filter requested (then there is no cross filter problem)
                    const shouldCountN = isExactMatch || combo.length === 1;
                    
                    if (!seenKeys.has(dataKey)) {
                        seenKeys.add(dataKey);
                        
                        if (shouldCountN) {
                            totalN += result.data.n;
                        }
                        
                        // always aggregate the values (for the chart, also with a fallback)
                        if (result.data.values) {
                            for (const [option, count] of Object.entries(result.data.values)) {
                                aggregatedValues[option] = (aggregatedValues[option] || 0) + Number(count);
                            }
                        }
                    }
                }
            }
            
            // NO exact match AND more than one filter requested -> n = 0
            const hasMultipleFilters = combinations.some(c => c.length > 1);
            if (!hasAnyExactMatch && hasMultipleFilters) {
                totalN = 0;
            }
            
            if (totalN === 0 && Object.keys(aggregatedValues).length === 0) {
                return null;
            }
            
            // recompute the percentages (only if n > 0)
            const percentages = {};
            if (totalN > 0) {
                for (const [option, count] of Object.entries(aggregatedValues)) {
                    percentages[option] = (count / totalN) * 100;
                }
            }
            
            return { 
                n: totalN, 
                values: aggregatedValues, 
                percentages,
                hasExactMatch: hasAnyExactMatch
            };
        },
        
        /**
         * Gets cross filter data with a fallback hierarchy (for a single combination)
         * @param {Object} question - the question
         * @param {Array} filters - active filters
         * @returns {Object} { data, usedFilters, isFallback }
         */
        getCrossFilterData(question, filters) {
            if (!question || !question.results) {
                return { data: null, usedFilters: [], isFallback: true };
            }
            
            // is filters an array?
            if (!Array.isArray(filters)) {
                console.error('getCrossFilterData: filters ist kein Array!', filters);
                return { data: null, usedFilters: [], isFallback: true };
            }
            
            // no filters active
            if (filters.length === 0) {
                return { data: question.results.all, usedFilters: [], isFallback: false };
            }
            
            // try the full combination
            const fullKey = this.buildCrossFilterKey(filters);
            const fullValueKey = this.buildCrossFilterValueKey(filters);
            
            if (question.results[fullKey]) {
            }
            
            if (question.results[fullKey]?.[fullValueKey]) {
                return { 
                    data: question.results[fullKey][fullValueKey], 
                    usedFilters: filters, 
                    isFallback: false 
                };
            }
            
            // fallback: try smaller combinations (with 3 filters -> pairs, with 2 -> singles)
            if (filters.length >= 2) {
                // try all pairs (with 3 filters)
                if (filters.length === 3) {
                    const combos2 = [
                        [filters[0], filters[1]],
                        [filters[0], filters[2]],
                        [filters[1], filters[2]]
                    ];
                    
                    for (const combo of combos2) {
                        const key = this.buildCrossFilterKey(combo);
                        const valueKey = this.buildCrossFilterValueKey(combo);
                        if (question.results[key]?.[valueKey]) {
                            return { 
                                data: question.results[key][valueKey], 
                                usedFilters: combo, 
                                isFallback: true 
                            };
                        }
                    }
                }
                
                // try the single filters
                for (const filter of filters) {
                    const key = `by_${filter.segment}`;
                    if (question.results[key]?.[filter.value]) {
                        return { 
                            data: question.results[key][filter.value], 
                            usedFilters: [filter], 
                            isFallback: true 
                        };
                    }
                }
            }
            
            // last fallback: "all"
            return { data: question.results.all, usedFilters: [], isFallback: true };
        },
        
        /**
         * Result data of a question based on the current filter
         */
        getQuestionData(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.results) return null;
            
            // "own selection" view
            if (this.viewMode === 'myprofile') {
                // complex question types: use the segment data directly (no aggregation possible)
                if (q.type === 'freetext' || q.type === 'cooperation_matrix' || q.type === 'matrix_likert') {
                    // no filter active -> all data
                    if (!this.isMyProfileFilterActive()) {
                        return q.results.all;
                    }
                    
                    // First dimension (configured order) with a single selected
                    // value and enough respondents
                    for (const f of this.singleProfileFilters()) {
                        const segData = q.results[`by_${f.segment}`]?.[f.value];
                        if (segData && segData.n >= this.minN()) {
                            return segData;
                        }
                    }
                    
                    // fallback to all data
                    return q.results.all;
                }
                
                // simple question types: use the multi-select aggregation
                return this.getAggregatedFilterData(q);
            }
            
            // always return "all" without an active filter or without segmented data
            if (this.filter.type === 'all' || this.filter.value === 'all') {
                return q.results.all;
            }
            
            // does segmented data exist at all?
            const segmentKey = `by_${this.filter.type}`;
            if (!q.results[segmentKey] || !q.results[segmentKey][this.filter.value]) {
                // Segment below the privacy threshold or not exported: show
                // "not enough data" instead of the overall result under a
                // filter label.
                return null;
            }
            
            return q.results[segmentKey][this.filter.value];
        },
        
        /**
         * Whether segmented data is available
         */
        /** Privacy threshold exported by the backend (sample_warnings.minimum_threshold). */
        minN() {
            const n = this.data?.metadata?.min_n || 5;
            if (typeof window !== 'undefined') window.surveyMinNValue = n;  // read by charts.js (surveyMinN())
            return n;
        },
        
        /** Free text: segment-specific clusters when requested and available. */
        clusterDataFor(resultData) {
            if (this.showSpecificClusters && resultData?.specific_clusters) {
                return resultData.specific_clusters;
            }
            return resultData;
        },
        
        /** Whether any free-text result of the current filter has segment-specific clusters. */
        hasSpecificClusters() {
            if (!this.data || this.filter.value === 'all') return false;
            const key = `by_${this.filter.type}`;
            return Object.values(this.data.questions || {}).some(
                q => q.results?.[key]?.[this.filter.value]?.specific_clusters);
        },
        
        hasSegmentedData() {
            // do segment definitions exist?
            const segments = this.data.segments || {};
            const hasSegmentDefs = Object.keys(segments).length > 0 && 
                                   Object.values(segments).some(s => s.values && s.values.length > 0);
            
            if (!hasSegmentDefs) return false;
            
            // does at least one question have segmented results?
            const questions = this.data.questions || {};
            for (const qid of Object.keys(questions)) {
                const q = questions[qid];
                if (this.hasAnySegmentResults(q.results)) {
                    return true;
                }
            }
            
            // segment definitions present, but no segmented results;
            // true anyway, so the interface is shown
            return hasSegmentDefs;
        },
        
        /**
         * Whether a specific question has segment data
         */
        hasQuestionSegmentData(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.results) return false;
            
            // does at least one segment have data?
            return this.hasAnySegmentResults(q.results);
        },
        
        /**
         * Toggles the small multiples of a question
         * @param {string} qid - question id
         * @param {string} viewPrefix - optional: prefix of the container id ('themes', etc.)
         */
        toggleSmallMultiples(qid, viewPrefix = null) {
            const stateKey = viewPrefix ? `${viewPrefix.charAt(0)}-${qid}` : qid;
            this.openSmallMultiples[stateKey] = !this.openSmallMultiples[stateKey];
            
            // render the small multiples when opened
            if (this.openSmallMultiples[stateKey]) {
                this.$nextTick(() => {
                    this.renderSmallMultiplesForQuestion(qid, viewPrefix);
                });
            }
        },
        
        /**
         * Renders the small multiples of a question
         * @param {string} qid - question id
         * @param {string} viewPrefix - optional: prefix of the container id
         */
        renderSmallMultiplesForQuestion(qid, viewPrefix = null) {
            const containerId = viewPrefix ? `sm-container-${viewPrefix.charAt(0)}-${qid}` : `sm-container-${qid}`;
            const container = document.getElementById(containerId);
            if (!container) {
                console.warn(`Small Multiples Container nicht gefunden: ${containerId}`);
                return;
            }
            
            const question = this.getQuestion(qid);
            if (!question) return;
            
            if (typeof renderSmallMultiples === 'function') {
                renderSmallMultiples(container, question, this.data.segments, this.lang);
            }
        },
        hasSegmentedResults() {
            const questions = this.data.questions || {};
            for (const qid of Object.keys(questions)) {
                const q = questions[qid];
                if (this.hasAnySegmentResults(q.results)) {
                    return true;
                }
            }
            return false;
        },
        
        // ============================================
        // CHART-RENDERING
        // ============================================
        
        /**
         * Renders all charts of the current view mode
         */
        renderAllCharts() {
            let questionIds = [];
            
            switch (this.viewMode) {
                case 'questions':
                    questionIds = this.getAllQuestionsOrdered();
                    break;
                case 'themes':
                    questionIds = this.getQuestionsForTheme();
                    break;
                case 'keywords':
                    if (this.selectedKeyword) {
                        questionIds = this.getQuestionsForKeyword(this.selectedKeyword);
                    }
                    break;
                case 'myprofile':
                    // only questions of the filtered groups (without demographics)
                    questionIds = this.getMyProfileQuestions();
                    break;
                default:
                    questionIds = this.getQuestionsForTheme();
            }
            
            for (const qid of questionIds) {
                this.renderChart(qid);
            }
        },
        
        /**
         * Questions for the "own selection" view (without the demographic questions)
         */
        getMyProfileQuestions() {
            const groups = this.getSurveyGroups(); // excludes the dimensions' source questions in the profile view
            const questionIds = [];
            
            for (const group of groups) {
                if (group.questions) {
                    for (const q of group.questions) {
                        const qid = typeof q === 'object' ? q.id : q;
                        if (this.data.questions?.[qid]) {
                            questionIds.push(qid);
                        }
                    }
                }
            }
            
            return questionIds;
        },
        
        /**
         * Renders a single chart
         */
        renderChart(qid) {
            const q = this.getQuestion(qid);
            
            // container id from the viewMode
            let containerId;
            switch (this.viewMode) {
                case 'themes':
                    containerId = `chart-t-${qid}`;
                    break;
                case 'keywords':
                    containerId = `chart-kw-${qid}`;
                    break;
                case 'myprofile':
                    containerId = `chart-myprofile-${qid}`;
                    break;
                default:
                    containerId = `chart-${qid}`;
            }
            
            const container = document.getElementById(containerId);
            
            if (!q || !container) {
                return; // container not found: normal for hidden views
            }
            
            const resultData = this.getQuestionData(qid);
            
            // is there enough data?
            if (!resultData) {
                renderNoData(container, this.lang, this.minN());
                return;
            }
            
            // with free text n may also be n_items or n_responses
            const n = resultData.n || resultData.n_items || resultData.n_responses || 0;
            if (n < this.minN()) {
                renderNoData(container, this.lang, this.minN());
                return;
            }
            
            // determine the chart type and call the matching function
            const chartType = q.type || 'single_choice';
            
            // profile view with an active filter: comparison chart for bar charts
            const useComparisonChart = this.viewMode === 'myprofile' && 
                                       this.isMyProfileFilterActive() &&
                                       ['single_choice', 'multi_choice', 'multi_choice_binary'].includes(chartType);
            
            try {
                if (useComparisonChart) {
                    // comparison chart: overall vs. filter
                    const allData = q.results?.all;
                    const filteredData = resultData;
                    const filterLabel = this.getFilterLabel();
                    renderComparisonChart(container, q, allData, filteredData, this.lang, filterLabel);
                } else {
                    // Standard-Rendering
                    switch (chartType) {
                        case 'single_choice':
                        case 'multi_choice':
                        case 'multi_choice_binary':
                            renderBarChart(container, q, resultData, this.lang);
                            if (resultData.nps) renderNpsNote(container, resultData.nps, this.lang);
                            if (resultData.scale_metrics) renderScaleMetrics(container, [{ label: '', metrics: resultData.scale_metrics }], this.lang,
                                    l => pickLang((q.options || []).find(o => Object.values(o.label || {}).includes(l))?.label, this.lang) || l);
                            break;
                        
                        case 'ranking':
                            renderRankingChart(container, q, resultData, this.lang);
                            break;
                            
                        case 'matrix_likert':
                        case 'matrix':
                            renderStackedBarChart(container, q, resultData, this.lang);
                            renderScaleMetrics(container, Object.entries(resultData.by_row || resultData.items || {})
                                .map(([k, r]) => ({ label: pickLang((q.rows || []).find(x => x.key === k)?.label, this.lang) || k, metrics: r.scale_metrics })), this.lang,
        l => pickLang((q.scale || []).find(o => Object.values(o.label || {}).includes(l))?.label, this.lang) || l);
                            break;
                        
                        case 'cooperation_matrix':
                            renderCooperationMatrixChart(container, q, resultData, this.lang);
                            break;
                            
                        case 'freetext_clustered':
                        case 'freetext':
                        case 'cluster':
                            renderClusterChart(container, q, this.clusterDataFor(resultData), this.lang);
                            break;
                        
                        case 'numeric':
                            renderNumericChart(container, q, resultData, this.lang);
                            break;
                        
                        case 'numeric_matrix':
                            renderNumericMatrixChart(container, q, resultData, this.lang);
                            break;
                        
                        case 'date':
                            renderDateChart(container, q, resultData, this.lang);
                            break;
                            
                        default:
                            renderBarChart(container, q, resultData, this.lang);
                    }
                }
            } catch (err) {
                console.error(`Error rendering ${qid}:`, err);
                container.innerHTML = `<div class="text-red-500 p-4">Fehler: ${escapeHtml(err.message)}</div>`;
            }
        },
        
        /**
         * Exports a chart as an image
         */
        exportChartImage(qid, format = 'png') {
            const q = this.getQuestion(qid);
            const filename = q ? `${q.id}_${this.lang}` : `chart_${qid}`;
            
            // container id from the viewMode
            let containerId;
            switch (this.viewMode) {
                case 'themes':
                    containerId = `chart-t-${qid}`;
                    break;
                case 'keywords':
                    containerId = `chart-kw-${qid}`;
                    break;
                case 'myprofile':
                    containerId = `chart-myprofile-${qid}`;
                    break;
                default:
                    containerId = `chart-${qid}`;
            }
            
            exportChart(containerId, filename, format);
        },
        
        // ============================================
        // LANGUAGE
        // ============================================
        
        /**
         * Switches the language
         */
        /** A per-language entry of the survey metadata (start page, summary, contact). */
        metaText(key) {
            return pickLang(this.data?.metadata?.[key], this.lang) || '';
        },
        
        /** First segmentation dimension: its label and number of groups (start page tile). */
        firstDimension() {
            const segs = this.data?.segments || {};
            const id = Object.keys(segs)[0];
            if (!id) return null;
            const seg = segs[id];
            const values = seg.values || seg.options || [];
            return { label: pickLang(seg.label, this.lang) || id, count: Array.isArray(values) ? values.length : Object.keys(values).length };
        },
        
        /** Summary sections from the metadata: [{title, text}] in the chosen language. */
        summarySections() {
            return (this.data?.metadata?.summary || []).map(sec => ({
                title: pickLang(sec.title, this.lang) || '', text: pickLang(sec.text, this.lang) || ''
            })).filter(sec => sec.title || sec.text);
        },
        
        /**
         * Languages offered by the switcher: those of the export (metadata.languages)
         * that have interface texts; without metadata every language with texts.
         */
        availableLanguages() {
            const ui = Object.keys(window.i18n || {});
            const exported = (this.data?.metadata?.languages || []).filter(l => ui.includes(l));
            return exported.length ? exported : ui;
        },
        
        /** Locale for numbers and dates in the chosen language. */
        numberLocale() {
            return { de: 'de-DE', fr: 'fr-FR', en: 'en-US', it: 'it-IT' }[this.lang] || 'en-US';
        },
        
        switchLang(newLang) {
            if (!this.availableLanguages().includes(newLang)) return;
            if (this.lang === newLang) return;
            
            this.lang = newLang;
            
            // update the URL hash
            this.updateUrlHash();
            
            // render the charts again for the translated labels
            this.$nextTick(() => {
                this.renderAllCharts();
            });
        },
        
        /**
         * Translates an interface text
         */
        t(key) {
            return t(key, this.lang);
        },
        
        // ============================================
        // HILFSMETHODEN
        // ============================================
        
        /**
         * Formats a number with thousands separators
         */
        formatNumber(num) {
            if (typeof num !== 'number') return '0';
            return num.toLocaleString(this.numberLocale());
        },
        
        /**
         * Label of a segment type
         */
        getSegmentLabelFallback(segmentType) {
            // labels come from dashboard_data.json (segments.<id>.label); without them the id
            return segmentType;
        },
        
        /**
         * Whether a theme has questions
         */
        themeHasQuestions(themeId) {
            const theme = this.themes.themes?.[themeId];
            return theme && theme.questions && theme.questions.length > 0;
        },
        
        // ============================================
        // CLUSTER ACCORDION METHODS
        // ============================================
        
        /**
         * Toggles the cluster details of a question
         */
        toggleClusterDetails(qid) {
            this.openClusters[qid] = !this.openClusters[qid];
        },
        
        /**
         * Toggles a single cluster
         */
        toggleSingleCluster(qid, idx) {
            const key = qid + '-' + idx;
            this.openSingleClusters[key] = !this.openSingleClusters[key];
        },
        
        /**
         * Toggles the question accordion in the segment cluster comparison
         */
        toggleSegmentClusterQuestion(questionId) {
            this.openSegmentClusterQuestions[questionId] = !this.openSegmentClusterQuestions[questionId];
        },
        
        /**
         * Toggles the segment accordion in the deviations view
         */
        toggleSegmentDeviationValue(segValueKey) {
            this.openSegmentDeviationValues[segValueKey] = !this.openSegmentDeviationValues[segValueKey];
        },
        
        /**
         * Cluster data of a question
         * Supports both formats:
         * - label, examples [{text, language}]
         * - name {de,fr,en,it}, examples_translated {de:[],fr:[],en:[],it:[]}
         */
        getClusterData(qid) {
            const q = this.getQuestion(qid);
            if (!q || q.type !== 'freetext') return [];
            
            const clusters = q.clusters || [];
            if (clusters.length === 0) return [];
            
            // the clusters carry results.all.percentage directly
            const clusterData = clusters.map(c => {
                // label: support both field names (name, label)
                const labelObj = c.name || c.label;
                const labelLang = pickLang(labelObj, this.lang) || c.id;
                
                // percentage and count from cluster.results.all
                const allResults = c.results?.all || {};
                const percentage = allResults.percentage || 0;
                const count = allResults.count || c.count || 0;
                const uniqueRespondents = allResults.unique_respondents || c.unique_respondents || count;
                const n = allResults.n || 0;
                
                // Description
                const description = c.description ? 
                    (pickLang(c.description, this.lang)) : null;
                
                // examples: support both formats
                // examples_translated {de: [...], fr: [...], en: [...]}
                // examples [{text, language}]
                let examples = [];
                if (c.examples_translated) {
                    // the array of the current language directly
                    examples = pickLang(c.examples_translated, this.lang) || [];
                } else if (c.examples) {
                    // examples of the export: [{lang: text}]
                    examples = c.examples;
                }
                
                return {
                    id: c.id,
                    label: labelLang,
                    percentage: percentage,
                    count: count,
                    uniqueRespondents: uniqueRespondents,
                    n: n,
                    description: description,
                    examples: examples,
                    examples_translated: c.examples_translated || null, // for getFilteredExamples
                    results: c.results // keep the complete results for the segment display
                };
            }).filter(c => c.percentage > 0 || c.count > 0);
            
            // sort by percentage
            return clusterData.sort((a, b) => b.percentage - a.percentage);
        },
        
        /**
         * Whether a free-text question is in item mode
         * Item mode: n_items != n_responses (several mentions per person)
         */
        isFreetextItemsMode(qid) {
            const q = this.getQuestion(qid);
            if (!q || q.type !== 'freetext') return false;
            
            const allResults = q.results?.all || {};
            const nItems = allResults.n_items || allResults.n || 0;
            const nResponses = allResults.n_responses || allResults.n || 0;
            
            return allResults.analysis_mode === 'items' || (nItems !== nResponses && nItems > 0);
        },
        
        /**
         * Statistics of a free-text question
         * For the caption below the chart
         */
        getFreetextStats(qid) {
            const q = this.getQuestion(qid);
            if (!q || q.type !== 'freetext') return null;
            
            const allResults = q.results?.all || {};
            
            return {
                nItems: allResults.n_items || allResults.n || 0,
                nResponses: allResults.n_responses || allResults.n || 0,
                analysisMode: allResults.analysis_mode || 'raw',
                avgItemsPerResponse: allResults.avg_items_per_response || null
            };
        },
        
        /**
         * The caption of a free-text question
         * Item mode: "X mentions by Y persons in total. Percentages = share of the persons."
         * Raw mode: "" (empty, the normal n display is enough)
         */
        getFreetextCaption(qid) {
            const stats = this.getFreetextStats(qid);
            if (!stats) return '';
            
            // a caption only in item mode (n_items != n_responses)
            if (stats.nItems === stats.nResponses || stats.nItems === 0) {
                return '';
            }
            
            const captions = {
                de: `Insgesamt ${stats.nItems} Nennungen von ${stats.nResponses} Personen. Prozente = Anteil der Personen (Mehrfachnennungen möglich).`,
                fr: `Au total ${stats.nItems} mentions de ${stats.nResponses} personnes. Pourcentages = part des personnes (mentions multiples possibles).`,
                en: `Total of ${stats.nItems} mentions from ${stats.nResponses} persons. Percentages = share of persons (multiple mentions possible).`,
                it: `In totale ${stats.nItems} menzioni da ${stats.nResponses} persone. Percentuali = quota di persone (menzioni multiple possibili).`
            };
            
            return pickLang(captions, this.lang);
        },
        
        /**
         * Filters examples by language and limits their number
         * Supports both formats:
         * - an array of strings (in the right language already, from examples_translated)
         * - an array of {text, language} objects
         * @param {Array} examples - array of examples
         * @param {number} maxCount - maximum number (default: 8)
         * @returns {Array} filtered examples as strings
         */
        getFilteredExamples(examples, maxCount = 8) {
            if (!examples || !Array.isArray(examples)) return [];
            if (examples.length === 0) return [];
            
            const currentLang = this.lang;
            let filtered = [];
            
            // check the format by the first element
            const firstExample = examples[0];
            
            if (typeof firstExample === 'string') {
                // format A: strings in the right language already
                filtered = examples.filter(ex => ex && ex.length > 0);
            } else if (typeof firstExample === 'object' && firstExample !== null) {
                // is it format B? (has language keys such as 'de', 'en', 'fr')
                const hasLangKeys = 'de' in firstExample || 'en' in firstExample || 'fr' in firstExample;
                
                if (hasLangKeys) {
                    // format B: objects with {de: "...", en: "...", fr: "...", segment: {...}}
                    filtered = examples
                        .map(ex => pickLang(ex, currentLang) || '')
                        .filter(text => text && text.length > 0);
                } else if (firstExample.text !== undefined) {
                    // format C: objects with {text, language}
                    filtered = examples
                        .filter(ex => ex.language === currentLang)
                        .map(ex => ex.text || '')
                        .filter(text => text && text.length > 0);
                }
            }
            
            // remove duplicates and limit
            const unique = [...new Set(filtered)];
            return unique.slice(0, maxCount);
        },
        
        /**
         * Colour of a cluster index (matching the chart colours)
         */
        getClusterColor(idx) {
            const palette = [
                '#2563eb', '#059669', '#d97706', '#dc2626', '#7c3aed',
                '#db2777', '#0891b2', '#65a30d', '#ca8a04', '#475569'
            ];
            return palette[idx % palette.length];
        },
        
        // ============================================
        // VIEW-MODE-NAVIGATION
        // ============================================
        
        /**
         * Switches the view mode
         */
        setViewMode(mode) {
            // A survey without themes has nothing to show under "themes"
            // (it would be an empty page): the question list instead
            if (mode === 'themes' && !Object.keys(this.themes?.themes || {}).length) mode = 'questions';
            this.viewMode = mode;
            this.selectedKeyword = null;
            this.keywordSearch = '';
            
            // theme view: activate the first theme if none is active
            if (mode === 'themes' && !this.activeTheme && this.themes.themes) {
                const themeIds = Object.keys(this.themes.themes);
                if (themeIds.length > 0) {
                    // sort by order if present
                    themeIds.sort((a, b) => {
                        const orderA = this.themes.themes[a]?.order || 999;
                        const orderB = this.themes.themes[b]?.order || 999;
                        return orderA - orderB;
                    });
                    this.activeTheme = themeIds[0];
                }
            }
            
            // update the URL hash
            this.updateUrlHash();
            
            // render the charts after the DOM update
            this.$nextTick(() => {
                setTimeout(() => {
                    if (mode === 'correlations') {
                        this.renderCorrelationCharts();
                    } else {
                        this.renderAllCharts();
                    }
                }, 200);  // a slightly longer timeout for DOM stability
            });
        },
        
        /**
         * All questions in the order of the survey structure
         */
        getAllQuestionsOrdered() {
            if (!this.surveyStructure || !this.surveyStructure.groups) {
                // fallback: order of data.questions
                return Object.keys(this.data.questions || {});
            }
            
            const orderedQuestions = [];
            const groups = [...this.surveyStructure.groups].sort((a, b) => a.order - b.order);
            
            for (const group of groups) {
                if (group.questions) {
                    for (const q of group.questions) {
                        const qid = typeof q === 'object' ? q.id : q;
                        if (this.data.questions?.[qid]) {
                            orderedQuestions.push(qid);
                        }
                    }
                }
            }
            
            // questions no group lists, as in getSurveyGroups
            for (const qid of Object.keys(this.data.questions || {})) {
                if (!orderedQuestions.includes(qid)) orderedQuestions.push(qid);
            }
            return orderedQuestions;
        },
        
        /**
         * Questions of the current view mode
         */
        getQuestionsForCurrentView() {
            switch (this.viewMode) {
                case 'questions':
                    return this.getAllQuestionsOrdered();
                    
                case 'themes':
                    return this.getQuestionsForTheme();
                    
                case 'keywords':
                    if (this.selectedKeyword) {
                        return this.getQuestionsForKeyword(this.selectedKeyword);
                    }
                    return [];
                    
                case 'myprofile':
                    return this.getMyProfileQuestions();
                    
                default:
                    return [];
            }
        },
        
        /**
         * The groups of the "all questions" view
         */
        getSurveyGroups() {
            let groups = [...(this.surveyStructure?.groups || [])].sort((a, b) => a.order - b.order);
            // Questions of the export that no group lists (no settings.report_groups,
            // or groups covering only part of the survey) would otherwise be missing
            // from "All questions": they get a group of their own, in export order
            const listed = new Set(groups.flatMap(g => this.getQuestionIdsForGroup(g)));
            const rest = Object.keys(this.data.questions || {}).filter(qid => !listed.has(qid));
            if (rest.length) {
                const key = groups.length ? 'more_questions' : 'view_all_questions';
                const label = Object.fromEntries(Object.keys(window.i18n || {}).map(l => [l, t(key, l)]));
                groups.push({ id: '_ungrouped', label, questions: rest, order: Infinity });
            }
            
            // Profile view: leave out the source questions of the segment
            // dimensions (filtering by a dimension makes its own question
            // trivial); groups that become empty are dropped. Group order is
            // the one configured in survey_structure.json.
            if (this.viewMode === 'myprofile') {
                const hidden = new Set(this.segmentSourceQuestions());
                groups = groups
                    .map(g => ({ ...g, questions: (g.questions || []).filter(q => !hidden.has(typeof q === 'object' ? q.id : q)) }))
                    .filter(g => g.questions.length > 0);
            }
            
            return groups;
        },
        
        /**
         * The question ids of a group
         */
        getQuestionIdsForGroup(group) {
            if (!group || !group.questions) return [];
            return group.questions.map(q => typeof q === 'object' ? q.id : q);
        },
        
        /**
         * Question label from the survey structure
         */
        getQuestionLabel(qid) {
            // look in the survey structure first
            if (this.surveyStructure?.groups) {
                for (const group of this.surveyStructure.groups) {
                    for (const q of group.questions || []) {
                        if (typeof q === 'object' && q.id === qid && q.label) {
                            return pickLang(q.label, this.lang) || qid;
                        }
                    }
                }
            }
            return this.getQuestionDisplayText(qid);
        },
        
        // ============================================
        // KEYWORD FUNCTIONS
        // ============================================
        
        /**
         * Collects all keywords from the cluster labels
         * Only the cluster labels are used (no examples: too much noise)
         */
        getAllKeywords() {
            const keywords = new Map(); // keyword -> { count, questions, clusters }
            
            for (const [qid, question] of Object.entries(this.data.questions || {})) {
                if (question.type === 'freetext' && question.clusters) {
                    for (const cluster of question.clusters) {
                        // cluster label as a keyword: STRICTLY the current language only
                        // support name and label
                        const labelObj = cluster.name || cluster.label;
                        const label = labelObj?.[this.lang];
                        
                        // only if the label exists in the current language
                        if (label && label.length > 3 && label.length <= 60) {
                            if (!keywords.has(label)) {
                                keywords.set(label, { count: 0, questions: new Set(), clusters: [] });
                            }
                            keywords.get(label).count++;
                            keywords.get(label).questions.add(qid);
                            keywords.get(label).clusters.push({ qid, clusterId: cluster.id, cluster });
                        }
                    }
                }
            }
            
            // convert into an array, filter and sort
            return Array.from(keywords.entries())
                .map(([keyword, data]) => ({
                    keyword,
                    count: data.count,
                    questions: Array.from(data.questions),
                    questionCount: data.questions.size,
                    clusters: data.clusters
                }))
                // at least 2 mentions in total required
                .filter(k => k.count >= 2)
                .sort((a, b) => {
                    // first by the number of questions, then by the total
                    if (b.questionCount !== a.questionCount) {
                        return b.questionCount - a.questionCount;
                    }
                    return b.count - a.count;
                });
        },
        
        /**
         * Filtered keywords based on the search term
         */
        getFilteredKeywords() {
            const allKeywords = this.getAllKeywords();
            
            if (!this.keywordSearch || this.keywordSearch.length < 2) {
                return allKeywords.slice(0, 50); // Top 50
            }
            
            const search = this.keywordSearch.toLowerCase();
            return allKeywords
                .filter(k => k.keyword.toLowerCase().includes(search))
                .slice(0, 50);
        },
        
        /**
         * Questions of a given keyword
         */
        getQuestionsForKeyword(keyword) {
            const allKeywords = this.getAllKeywords();
            const found = allKeywords.find(k => k.keyword === keyword);
            return found ? found.questions : [];
        },
        
        /**
         * Selects a keyword
         */
        selectKeyword(keyword) {
            this.selectedKeyword = keyword;
            
            // update the URL hash
            this.updateUrlHash();
            
            // important: render the charts after the DOM update with a longer delay
            const renderKeywordCharts = () => {
                const questionIds = this.getQuestionsForKeyword(keyword);
                for (const qid of questionIds) {
                    // the keywords view has its own chart containers with the prefix 'kw-'
                    const container = document.getElementById(`chart-kw-${qid}`);
                    if (container) {
                        this.renderChartInContainer(container, qid);
                    } else {
                        // Retry after short delay
                        setTimeout(() => {
                            const retryContainer = document.getElementById(`chart-kw-${qid}`);
                            if (retryContainer) {
                                this.renderChartInContainer(retryContainer, qid);
                            }
                        }, 300);
                    }
                }
            };
            
            this.$nextTick(() => {
                setTimeout(renderKeywordCharts, 150);
            });
        },
        
        /**
         * Renders a chart in a specific container
         */
        renderChartInContainer(container, qid) {
            const q = this.getQuestion(qid);
            if (!q) {
                return;
            }
            
            const resultData = this.getQuestionData(qid);
            if (!resultData) {
                renderNoData(container, this.lang, this.minN());
                return;
            }
            
            const n = resultData.n || resultData.n_items || resultData.n_responses || 0;
            if (n < this.minN()) {
                renderNoData(container, this.lang, this.minN());
                return;
            }
            
            const chartType = q.type || 'single_choice';
            
            try {
                switch (chartType) {
                    case 'freetext_clustered':
                    case 'freetext':
                    case 'cluster':
                        renderClusterChart(container, q, this.clusterDataFor(resultData), this.lang);
                        break;
                    
                    case 'numeric':
                        renderNumericChart(container, q, resultData, this.lang);
                        break;
                    
                    case 'numeric_matrix':
                        renderNumericMatrixChart(container, q, resultData, this.lang);
                        break;
                    
                    case 'date':
                        renderDateChart(container, q, resultData, this.lang);
                        break;
                    
                    default:
                        renderBarChart(container, q, resultData, this.lang);
                }
            } catch (err) {
                console.error(`Error rendering ${qid}:`, err);
                container.innerHTML = `<div class="text-red-500 p-4">Fehler: ${escapeHtml(err.message)}</div>`;
            }
        },
        
        /**
         * Computes the font size for the word cloud
         * @param {number} count - count of this keyword
         * @param {number} maxCount - maximum of all keywords
         */
        getKeywordFontSize(count, maxCount) {
            const minSize = 13;
            const maxSize = 26;
            // logarithmic scaling for a better distribution with low values
            const logCount = Math.log(count + 1);
            const logMax = Math.log(maxCount + 1);
            const ratio = logMax > 0 ? logCount / logMax : 0;
            return Math.round(minSize + ratio * (maxSize - minSize));
        },
        
        // ============================================
        // PROFILE FILTER (OWN SELECTION)
        // ============================================
        
        /**
         * Switches the "own selection" filter on/off
         */
        toggleMyProfile() {
            this.myProfile.active = !this.myProfile.active;
            this.applyFilter();
        },
        
        /**
         * Applies the "own selection" filter (button click)
         * Copies the selected filters into appliedFilters and computes n
         */
        applyMyProfileFilter() {
            
            // copy the selected filters into the applied filters (as copies of the arrays!)
            for (const id of this.getAvailableSegmentIds()) {
                this.appliedFilters[id] = [...(this.myProfile[id] || [])];
            }
            
            console.log('appliedFilters vor n-Berechnung:', JSON.stringify(this.appliedFilters));
            
            // compute the aggregated n of the applied filters
            this.appliedFilters.n = this.calculateAggregatedN();
            
            
            // render the charts again
            this.renderAllCharts();
        },
        
        /**
         * Resets all profile filters
         */
        resetMyProfileFilter() {
            for (const id of this.getAvailableSegmentIds()) {
                this.myProfile[id] = [];
                this.appliedFilters[id] = [];
            }
            this.appliedFilters.n = this.data.metadata?.total_responses || 0;
            
            this.renderAllCharts();
        },
        
        /**
         * Computes the aggregated n of all filter combinations
         * With cross filters without exact data it checks whether a fallback was used
         */
        calculateAggregatedN() {
            if (!this.isMyProfileFilterActive()) {
                return this.data.metadata?.total_responses || 0;
            }
            
            const combinations = this.getAllFilterCombinations();
            
            // find a single-choice question for the n computation
            const questions = this.data.questions || {};
            const demographicQuestions = this.segmentSourceQuestions();
            
            for (const qid of Object.keys(questions)) {
                const q = questions[qid];
                if (q.type !== 'single_choice' || !q.results?.all) continue;
                if (demographicQuestions.includes(qid)) continue;
                
                // compute n for this question
                let totalN = 0;
                const seenKeys = new Set();
                let foundAnyData = false;
                
                for (const combo of combinations) {
                    const result = this.getCrossFilterData(q, combo);
                    
                    if (result.data && result.data.n) {
                        foundAnyData = true;
                        
                        // is it an exact match (not fallen back to fewer filters)?
                        const isExactMatch = !result.isFallback;
                        
                        // a unique key for this data (based on the filters actually used)
                        const dataKey = this.buildCrossFilterKey(result.usedFilters) + '|' + 
                                       this.buildCrossFilterValueKey(result.usedFilters);
                        
                        // count only if:
                        // 1. exact match (no fallback)
                        // 2. OR only one filter set (then there is no cross filter problem)
                        if (!seenKeys.has(dataKey)) {
                            seenKeys.add(dataKey);
                            
                            if (isExactMatch || combo.length === 1) {
                                totalN += result.data.n;
                            }
                        }
                    }
                }
                
                
                // data found: return the result
                if (foundAnyData) {
                    return totalN;
                }
            }
            
            return 0;
        },
        
        /**
         * Values of a segment dimension: [{key, label: {lang: text}}, ...]
         */
        getSegmentValues(segmentKey) {
            return (this.data.segments?.[segmentKey]?.values || []).map(v => v.key);
        },
        
        /**
         * Value objects of a segment dimension: [{key, label}]
         */
        getSegmentValueObjects(segmentKey) {
            return this.data.segments?.[segmentKey]?.values || [];
        },
        
        /**
         * The label of a segment value
         */
        getSegmentValueLabel(segmentKey, valueKey, lang = null) {
            const useLang = lang || this.lang;
            const value = (this.data.segments?.[segmentKey]?.values || []).find(v => v.key === valueKey);
            return pickLang(value?.label, useLang) || valueKey;
        },
        
        /**
         * The label of an answer option
         */
        getOptionLabel(question, optionKey, lang = null) {
            const useLang = lang || this.lang;
            const options = question?.options;
            
            if (!options) return optionKey;
            
            // [{key, label}]
            const option = Array.isArray(options) ? options.find(o => o.key === optionKey) : null;
            return pickLang(option?.label, useLang) || optionKey;
        },
        
        /**
         * All option keys of a question
         */
        getOptionKeys(question) {
            const options = question?.options;
            
            if (!options) return [];
            
            // [{key, label}]
            return Array.isArray(options) ? options.map(o => o.key) : [];
        },
        
        /**
         * Free-text cluster data of a question (questions.{id}.clusters), or null
         */
        getFreetextClusterData(questionId) {
            // primary: the clusters directly in the question (per specification)
            const question = this.getQuestion(questionId);
            if (question?.clusters?.length > 0) {
                return {
                    question_id: questionId,
                    total_responses: question.results?.all?.n || 0,
                    clusters: question.clusters
                };
            }
            
            return null;
        },
        
        /**
         * Whether the free-text cluster data is segmented
         */
        hasSegmentedClusterData(questionId) {
            const clusterData = this.getFreetextClusterData(questionId);
            if (!clusterData?.clusters?.length) return false;
            
            // does at least one cluster have results.by_*?
            return clusterData.clusters.some(cluster => {
                const results = cluster.results;
                return this.hasAnySegmentResults(results);
            });
        },
        
        /**
         * Filters the examples of free-text clusters by the active filters
         */
        getFilteredClusterExamples(cluster, maxExamples = 3) {
            if (!cluster?.examples) return [];
            
            const examples = cluster.examples.filter(example => {
                const seg = example.segment;
                if (!seg) return true;  // always show examples without a segment
                
                // Every dimension with selected values must match
                return this.getAvailableSegmentIds().every(id => {
                    const selected = this.appliedFilters[id] || [];
                    return selected.length === 0 || selected.includes(seg[id]);
                });
            });
            
            // sort: the current language first
            examples.sort((a, b) => {
                if (a.language === this.lang && b.language !== this.lang) return -1;
                if (a.language !== this.lang && b.language === this.lang) return 1;
                return 0;
            });
            
            return examples.slice(0, maxExamples);
        },
        
        
        /**
         * Scrolls to an element
         */
        scrollToQuestion(qid) {
            const element = document.getElementById(`question-${qid}`);
            if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        },
        
        /**
         * Scrolls to a question group
         */
        scrollToGroup(groupId) {
            const element = document.getElementById(`group-${groupId}`);
            if (element) {
                element.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        },
        
        /**
         * Formats an ISO date
         */
        formatDate(isoDate) {
            if (!isoDate) return '-';
            try {
                const date = new Date(isoDate);
                const options = { year: 'numeric', month: 'long', day: 'numeric' };
                const locale = this.numberLocale();
                return date.toLocaleDateString(locale, options);
            } catch {
                return isoDate;
            }
        },
        
        /**
         * Badge text of a question type
         */
        getQuestionTypeBadge(type) {
            const badges = {
                single_choice: { de: 'Einfachauswahl', fr: 'Choix unique', en: 'Single choice', it: 'Scelta singola' },
                multi_choice: { de: 'Mehrfachauswahl', fr: 'Choix multiple', en: 'Multiple choice', it: 'Scelta multipla' },
                multi_choice_binary: { de: 'Mehrfachauswahl', fr: 'Choix multiple', en: 'Multiple choice', it: 'Scelta multipla' },
                numeric: { de: 'Numerisch', fr: 'Numérique', en: 'Numeric', it: 'Numerico' },
                ranking: { de: 'Ranking', fr: 'Classement', en: 'Ranking', it: 'Classifica' },
                matrix_likert: { de: 'Matrix/Likert', fr: 'Matrice/Likert', en: 'Matrix/Likert', it: 'Matrice/Likert' },
                matrix: { de: 'Matrix', fr: 'Matrice', en: 'Matrix', it: 'Matrice' },
                cooperation_matrix: { de: 'Kooperationsmatrix', fr: 'Matrice de coopération', en: 'Cooperation Matrix', it: 'Matrice di cooperazione' },
                freetext_clustered: { de: 'Freitext (gruppiert)', fr: 'Texte libre (groupé)', en: 'Free text (clustered)', it: 'Testo libero (raggruppato)' },
                freetext: { de: 'Freitext (gruppiert)', fr: 'Texte libre (groupé)', en: 'Free text (clustered)', it: 'Testo libero (raggruppato)' },
                cluster: { de: 'Cluster-Analyse', fr: 'Analyse de cluster', en: 'Cluster analysis', it: 'Analisi dei cluster' }
            };
            return badges[type]?.[this.lang] || type || '-';
        },
        
        /**
         * Converts an id into readable text
         * e.g. "usage_frequency" -> "Usage Frequency"
         */
        humanizeId(id) {
            if (!id) return '';
            return id
                .replace(/_/g, ' ')
                .replace(/-/g, ' ')
                .replace(/\b\w/g, l => l.toUpperCase());
        },
        
        /**
         * Question text with a fallback to the humanised id
         */
        getQuestionDisplayText(qid) {
            const q = this.getQuestion(qid);
            if (!q) return this.humanizeId(qid);
            
            // does the text object exist and is it not empty?
            if (q.text && typeof q.text === 'object') {
                const text = pickLang(q.text, this.lang) || q.text['en'];
                if (text) return text;
            }
            
            // fallback: fallback texts for known questions
            // (no built-in question texts: they come from the export)
            
            // last fallback: the humanised id
            return this.humanizeId(qid);
        },
        
        /**
         * The help text of a question
         * @param {string} qid - question id
         * @returns {string|null} help text or null
         */
        getQuestionHelp(qid) {
            const q = this.getQuestion(qid);
            if (!q || !q.help) return null;
            
            // does the help object exist?
            if (q.help && typeof q.help === 'object') {
                const help = pickLang(q.help, this.lang) || q.help['en'];
                if (help) return help;
            }
            
            return null;
        },
        
        // ============================================
        // CORRELATION METHODS
        // ============================================
        
        /**
         * Correlation data if available
         */
        getCorrelationsData() {
            // use the loaded correlationsData (the JSON first, then the JS fallback)
            return this.correlationsData || null;
        },
        
        /**
         * Summary of the correlations
         */
        getCorrelationsSummary() {
            const data = this.getCorrelationsData();
            if (!data || !data.summary) {
                return { total: 0, strong: 0, medium: 0, weak: 0 };
            }
            return {
                total: data.summary.significant_correlations_found || 0,
                strong: data.summary.strong_correlations || 0,
                medium: data.summary.medium_correlations || 0,
                weak: data.summary.weak_correlations || 0
            };
        },
        
        /**
         * Filtered theme correlations
         */
        getFilteredThemeCorrelations() {
            const data = this.getCorrelationsData();
            if (!data || !data.theme_correlations) return {};
            return data.theme_correlations;
        },
        
        /**
         * Strength of the export (correlation.strength: strong/medium/weak/negligible)
         */
        normalizeStrength(strength) {
            if (!strength) return null;
            const s = strength.toLowerCase();
            if (s === 'strong') return 'strong';
            if (s === 'medium') return 'medium';
            if (s === 'weak') return 'weak';
            if (s === 'negligible') return 'weak'; // negligible → weak
            return s;
        },
        
        /**
         * Strength of a correlation (correlation.strength)
         */
        getCorrelationStrength(corr) {
            return this.normalizeStrength(corr.correlation?.strength);
        },
        
        /**
         * Filtered correlations of a theme
         */
        getFilteredCorrelationsForTheme(themeData) {
            if (!themeData || !themeData.correlations) return [];
            
            if (this.correlationStrengthFilter === 'all') {
                return themeData.correlations;
            }
            
            const filtered = themeData.correlations.filter(corr => {
                const strength = this.getCorrelationStrength(corr);
                return strength === this.correlationStrengthFilter;
            });
            
            console.log(`Filter: ${this.correlationStrengthFilter}, Gefiltert: ${filtered.length}/${themeData.correlations.length}`);
            return filtered;
        },
        
        /**
         * Whether cross-theme correlations are present
         */
        hasCrossThemeCorrelations() {
            const data = this.getCorrelationsData();
            return data?.cross_theme_correlations?.correlations?.length > 0;
        },
        
        /**
         * Filtered cross-theme correlations
         */
        getFilteredCrossThemeCorrelations() {
            const data = this.getCorrelationsData();
            if (!data?.cross_theme_correlations?.correlations) return [];
            
            if (this.correlationStrengthFilter === 'all') {
                return data.cross_theme_correlations.correlations;
            }
            
            return data.cross_theme_correlations.correlations.filter(corr => {
                const strength = this.getCorrelationStrength(corr);
                return strength === this.correlationStrengthFilter;
            });
        },
        
        /**
         * Theme label of an id
         */
        getThemeLabel(themeId) {
            const data = this.getCorrelationsData();
            if (data?.theme_correlations?.[themeId]?.label) {
                const label = data.theme_correlations[themeId].label;
                return pickLang(label, this.lang) || themeId;
            }
            return themeId;
        },
        
        /**
         * Colour by the strength of the correlation
         */
        getStrengthColor(strength) {
            const s = strength ? strength.toLowerCase() : '';
            switch (s) {
                case 'strong': 
                    return 'text-red-600';
                case 'medium': 
                    return 'text-amber-600';
                case 'weak': 
                case 'negligible':
                    return 'text-slate-500';
                default: 
                    return 'text-slate-600';
            }
        },
        
        /**
         * Badge class by the strength of the correlation
         */
        getStrengthBadgeClass(strength) {
            const s = strength ? strength.toLowerCase() : '';
            switch (s) {
                case 'strong': 
                    return 'bg-red-100 text-red-700';
                case 'medium': 
                    return 'bg-amber-100 text-amber-700';
                case 'weak': 
                case 'negligible':
                    return 'bg-slate-100 text-slate-600';
                default: 
                    return 'bg-slate-100 text-slate-600';
            }
        },
        
        /**
         * Renders the correlation charts (heatmap and network)
         */
        renderCorrelationCharts() {
            console.log('=== renderCorrelationCharts aufgerufen ===');
            console.log('correlationView:', this.correlationView);
            console.log('correlationStrengthFilter:', this.correlationStrengthFilter);
            
            // render the heatmap only when visible
            if (this.correlationView === 'heatmap') {
                this.$nextTick(() => {
                    setTimeout(() => this.renderCorrelationHeatmap(), 100);
                });
            }
            // render the network only when visible
            if (this.correlationView === 'network') {
                this.$nextTick(() => {
                    setTimeout(() => this.renderCorrelationNetwork(), 100);
                });
            }
        },
        
        /**
         * Renders the correlation heatmap with Plotly
         */
        renderCorrelationHeatmap() {
            console.log('=== renderCorrelationHeatmap aufgerufen ===');
            console.log('correlationStrengthFilter:', this.correlationStrengthFilter);
            
            const container = document.getElementById('correlation-heatmap');
            if (!container) {
                console.log('Container nicht gefunden!');
                return;
            }
            
            const data = this.getCorrelationsData();
            if (!data) {
                container.innerHTML = '<p class="text-center text-slate-500 py-8">' + t('no_correlations', this.lang) + '</p>';
                return;
            }
            
            // collect all FILTERED correlations
            const allCorrelations = [];
            const questionLabels = new Map();
            
            // theme correlations (filtered)
            Object.values(data.theme_correlations || {}).forEach(theme => {
                const filteredCorrs = this.getFilteredCorrelationsForTheme(theme);
                filteredCorrs.forEach(corr => {
                    const q1 = corr.question_1;
                    const q2 = corr.question_2;
                    if (q1 && q2) {
                        questionLabels.set(q1.id, pickLang(q1.label, this.lang) || q1.id);
                        questionLabels.set(q2.id, pickLang(q2.label, this.lang) || q2.id);
                        // correlation.coefficient, fallback: statistics.cramers_v
                        allCorrelations.push({
                            q1: q1.id,
                            q2: q2.id,
                            value: corr.correlation?.coefficient || 0, strength: this.getCorrelationStrength(corr), measure: corr.correlation?.effect_measure
                        });
                    }
                });
            });
            
            // cross-theme correlations (filtered)
            const filteredCrossTheme = this.getFilteredCrossThemeCorrelations();
            filteredCrossTheme.forEach(corr => {
                const q1 = corr.question_1;
                const q2 = corr.question_2;
                if (q1 && q2) {
                    questionLabels.set(q1.id, pickLang(q1.label, this.lang) || q1.id);
                    questionLabels.set(q2.id, pickLang(q2.label, this.lang) || q2.id);
                    // correlation.coefficient, fallback: statistics.cramers_v
                    allCorrelations.push({
                        q1: q1.id,
                        q2: q2.id,
                        value: corr.correlation?.coefficient || 0, strength: this.getCorrelationStrength(corr), measure: corr.correlation?.effect_measure
                    });
                }
            });
            
            if (allCorrelations.length === 0) {
                console.log('Keine Korrelationen gefunden für Filter:', this.correlationStrengthFilter);
                const msgKey = this.correlationStrengthFilter !== 'all' ? 'no_correlations_for_filter' : 'no_correlations';
                container.innerHTML = '<p class="text-center text-slate-500 py-8">' + t(msgKey, this.lang) + '</p>';
                return;
            }
            
            console.log('Gefilterte Korrelationen gesamt:', allCorrelations.length);
            
            // Unique questions
            const questions = Array.from(questionLabels.keys());
            const labels = questions.map(q => questionLabels.get(q));
            
            // create the matrix
            const n = questions.length;
            // Colour = strength class (weak/medium/strong): V, tau-b and epsilon squared
            // are on different scales, the classes are comparable; hover shows the measure.
            const level = { weak: 1 / 3, medium: 2 / 3, strong: 1 };
            const matrix = Array(n).fill(null).map(() => Array(n).fill(null));
            const hover = Array(n).fill(null).map(() => Array(n).fill(''));
            
            allCorrelations.forEach(corr => {
                const i = questions.indexOf(corr.q1);
                const j = questions.indexOf(corr.q2);
                if (i !== -1 && j !== -1) {
                    const info = t('measure_' + corr.measure, this.lang) + ': ' + (corr.value || 0).toFixed(2)
                        + ' (' + t('corr_' + corr.strength, this.lang) + ')';
                    matrix[i][j] = matrix[j][i] = level[corr.strength] || 0;
                    hover[i][j] = hover[j][i] = info;
                }
            });
            
            // shorten the labels if needed for readability
            const maxLabelLength = 40;
            const shortLabels = labels.map(l => 
                l.length > maxLabelLength ? l.substring(0, maxLabelLength) + '...' : l
            );
            
            // render the heatmap
            const trace = {
                type: 'heatmap',
                z: matrix,
                x: shortLabels,
                y: shortLabels,
                text: hover,
                colorscale: [
                    [0, '#f8fafc'],
                    [0.34, '#fee2e2'],
                    [0.67, '#f97316'],
                    [1, '#dc2626']
                ],
                zmin: 0,
                zmax: 1,
                colorbar: { tickvals: [1 / 3, 2 / 3, 1],
                            ticktext: ['weak', 'medium', 'strong'].map(s => t('corr_' + s, this.lang)) },
                hovertemplate: '<b>%{x}</b><br>↔ <b>%{y}</b><br>%{text}<extra></extra>'
            };
            
            // dynamic margin based on the longest label
            const maxLen = Math.max(...shortLabels.map(l => l.length));
            const dynamicMargin = Math.min(400, Math.max(250, maxLen * 6));
            
            const layout = {
                margin: { l: dynamicMargin, r: 50, t: 50, b: dynamicMargin },
                xaxis: {
                    tickangle: -45,
                    tickfont: { size: 11 },
                    automargin: true
                },
                yaxis: {
                    tickfont: { size: 11 },
                    automargin: true
                }
            };
            
            Plotly.newPlot(container, [trace], layout, { responsive: true });
        },
        
        /**
         * Renders the correlation network with D3.js
         */
        renderCorrelationNetwork() {
            console.log('=== renderCorrelationNetwork aufgerufen ===');
            console.log('correlationStrengthFilter:', this.correlationStrengthFilter);
            
            const container = document.getElementById('correlation-network');
            if (!container) {
                console.log('Container nicht gefunden!');
                return;
            }
            
            const data = this.getCorrelationsData();
            if (!data) {
                container.innerHTML = '<p class="text-center text-slate-500 py-8">' + t('no_correlations', this.lang) + '</p>';
                return;
            }
            
            // empty the container
            container.innerHTML = '';
            
            // collect the nodes and links (FILTERED)
            const nodesMap = new Map();
            const links = [];
            
            // theme correlations (filtered)
            Object.entries(data.theme_correlations || {}).forEach(([themeId, theme]) => {
                const filteredCorrs = this.getFilteredCorrelationsForTheme(theme);
                filteredCorrs.forEach(corr => {
                    const q1 = corr.question_1;
                    const q2 = corr.question_2;
                    if (q1 && q2) {
                        nodesMap.set(q1.id, {
                            id: q1.id,
                            label: pickLang(q1.label, this.lang) || q1.id,
                            theme: themeId
                        });
                        nodesMap.set(q2.id, {
                            id: q2.id,
                            label: pickLang(q2.label, this.lang) || q2.id,
                            theme: themeId
                        });
                        // correlation.coefficient, fallback: statistics.cramers_v
                        links.push({
                            source: q1.id,
                            target: q2.id,
                            value: corr.correlation?.coefficient || 0, strength: this.getCorrelationStrength(corr), measure: corr.correlation?.effect_measure
                        });
                    }
                });
            });
            
            // cross-theme correlations (filtered)
            const filteredCrossTheme = this.getFilteredCrossThemeCorrelations();
            filteredCrossTheme.forEach(corr => {
                const q1 = corr.question_1;
                const q2 = corr.question_2;
                if (q1 && q2) {
                    if (!nodesMap.has(q1.id)) {
                        nodesMap.set(q1.id, {
                            id: q1.id,
                            label: pickLang(q1.label, this.lang) || q1.id,
                            theme: corr.themes_involved?.[0] || 'cross'
                        });
                    }
                    if (!nodesMap.has(q2.id)) {
                        nodesMap.set(q2.id, {
                            id: q2.id,
                            label: pickLang(q2.label, this.lang) || q2.id,
                            theme: corr.themes_involved?.[1] || 'cross'
                        });
                    }
                    // correlation.coefficient, fallback: statistics.cramers_v
                    links.push({
                        source: q1.id,
                        target: q2.id,
                        value: corr.correlation?.coefficient || 0, strength: this.getCorrelationStrength(corr), measure: corr.correlation?.effect_measure
                    });
                }
            });
            
            const nodes = Array.from(nodesMap.values());
            
            if (nodes.length === 0) {
                const msgKey = this.correlationStrengthFilter !== 'all' ? 'no_correlations_for_filter' : 'no_correlations';
                container.innerHTML = '<p class="text-center text-slate-500 py-8">' + t(msgKey, this.lang) + '</p>';
                return;
            }
            
            // D3 force-directed graph
            const width = container.clientWidth || 800;
            const height = 600;
            
            // theme colours
            const themeColors = {
                'ki_digitalisierung': '#3b82f6',
                'it_sicherheit': '#ef4444',
                'cloud_infrastruktur': '#10b981',
                'organisation_governance': '#8b5cf6',
                'cross': '#6b7280'
            };
            
            const svg = d3.select(container)
                .append('svg')
                .attr('width', width)
                .attr('height', height);
            
            // allow zooming
            const g = svg.append('g');
            
            svg.call(d3.zoom()
                .scaleExtent([0.3, 3])
                .on('zoom', (event) => {
                    g.attr('transform', event.transform);
                }));
            
            // Simulation
            const simulation = d3.forceSimulation(nodes)
                .force('link', d3.forceLink(links).id(d => d.id).distance(150))
                .force('charge', d3.forceManyBody().strength(-400))
                .force('center', d3.forceCenter(width / 2, height / 2))
                .force('collision', d3.forceCollide().radius(50));
            
            // Links
            const link = g.append('g')
                .selectAll('line')
                .data(links)
                .join('line')
                .attr('stroke', d => {
                    if (d.strength === 'strong') return '#dc2626';
                    if (d.strength === 'medium') return '#f59e0b';
                    return '#cbd5e1';
                })
                .attr('stroke-width', d => {
                    if (d.strength === 'strong') return 4;
                    if (d.strength === 'medium') return 2;
                    return 1;
                })
                .attr('stroke-opacity', 0.8);
            
            // Nodes
            const node = g.append('g')
                .selectAll('g')
                .data(nodes)
                .join('g')
                .call(d3.drag()
                    .on('start', (event, d) => {
                        if (!event.active) simulation.alphaTarget(0.3).restart();
                        d.fx = d.x;
                        d.fy = d.y;
                    })
                    .on('drag', (event, d) => {
                        d.fx = event.x;
                        d.fy = event.y;
                    })
                    .on('end', (event, d) => {
                        if (!event.active) simulation.alphaTarget(0);
                        d.fx = null;
                        d.fy = null;
                    }));
            
            // Node circles
            node.append('circle')
                .attr('r', 20)
                .attr('fill', d => themeColors[d.theme] || '#64748b')
                .attr('stroke', '#fff')
                .attr('stroke-width', 2);
            
            // Node labels
            node.append('text')
                .text(d => d.label.length > 20 ? d.label.substring(0, 18) + '...' : d.label)
                .attr('x', 25)
                .attr('y', 5)
                .attr('font-size', '11px')
                .attr('fill', '#334155');
            
            // Tooltip
            node.append('title')
                .text(d => d.label);
            
            // Simulation update
            simulation.on('tick', () => {
                link
                    .attr('x1', d => d.source.x)
                    .attr('y1', d => d.source.y)
                    .attr('x2', d => d.target.x)
                    .attr('y2', d => d.target.y);
                
                node.attr('transform', d => `translate(${d.x},${d.y})`);
            });
        },
        
        // ============================================
        // SEGMENT COMPARISON FUNCTIONS
        // ============================================
        
        /**
         * Whether free-text clusters with segment data are present
         */
        hasFreetextClustersWithSegmentData() {
            const segKey = `by_${this.activeComparisonDimension()}`;
            const questions = this.data.questions || {};
            for (const q of Object.values(questions)) {
                if (q.type === 'freetext' && q.clusters) {
                    for (const cluster of q.clusters) {
                        if (cluster.results?.[segKey] && Object.keys(cluster.results[segKey]).length > 0) {
                            return true;
                        }
                    }
                }
            }
            return false;
        },
        
        /**
         * URL of a footer link (settings.survey_metadata.links), a string or one per language; '' if not configured.
         */
        footerLink(key) {
            const v = this.data?.metadata?.links?.[key];
            if (!v) return '';
            return typeof v === 'string' ? v : (pickLang(v, this.lang) || '');
        },
        
        /**
         * Respondents of a group: the largest base of the group over all questions.
         */
        groupSize(segKey, valueKey) {
            let n = 0;
            for (const q of Object.values(this.data.questions || {})) {
                const v = q.results?.[segKey]?.[valueKey]?.n;
                if (typeof v === 'number' && v > n) n = v;
            }
            return n;
        },
        
        /**
         * All segment values with cluster data
         */
        getSegmentValuesWithClusterData() {
            const segKey = `by_${this.activeComparisonDimension()}`;
            const segValues = new Map(); // key -> { key, label, totalN }
            const questions = this.data.questions || {};
            const segments = this.data.segments || {};
            
            for (const q of Object.values(questions)) {
                if (q.type === 'freetext' && q.clusters) {
                    for (const cluster of q.clusters) {
                        const byValue = cluster.results?.[segKey];
                        if (byValue) {
                            for (const [segValueKey, data] of Object.entries(byValue)) {
                                if (!segValues.has(segValueKey) && data.n >= this.minN()) {
                                    // get the label from the segments
                                    let label = segValueKey;
                                    if (segments[this.activeComparisonDimension()]?.values) {
                                        const found = segments[this.activeComparisonDimension()].values.find(v => v.key === segValueKey);
                                        if (found?.label) {
                                            label = pickLang(found.label, this.lang) || segValueKey;
                                        }
                                    }
                                    // the size of the group (not the n of this one question)
                                    segValues.set(segValueKey, { 
                                        key: segValueKey, 
                                        label: label,
                                        totalN: this.groupSize(segKey, segValueKey)
                                    });
                                }
                            }
                        }
                    }
                }
            }
            
            // sort by n (largest first)
            return Array.from(segValues.values()).sort((a, b) => b.totalN - a.totalN);
        },
        
        /**
         * Computes the cluster deviations of a segment value
         * @returns array of { clusterId, clusterLabel, questionId, questionLabel, allPercentage, segValuePercentage, deviation }
         */
        getClusterDeviationsForSegmentValue(segValueKey) {
            const segKey = `by_${this.activeComparisonDimension()}`;
            const deviations = [];
            const questions = this.data.questions || {};
            
            for (const [qid, q] of Object.entries(questions)) {
                if (q.type === 'freetext' && q.clusters) {
                    const questionLabel = pickLang(q.text, this.lang) || qid;
                    
                    for (const cluster of q.clusters) {
                        const allData = cluster.results?.all;
                        const segValueData = cluster.results?.[segKey]?.[segValueKey];
                        
                        if (allData && segValueData && segValueData.n >= this.minN()) {
                            const allPct = allData.percentage || 0;
                            const segValuePct = segValueData.percentage || 0;
                            const deviation = segValuePct - allPct;
                            
                            // cluster label: support name and label
                            const labelObj = cluster.name || cluster.label;
                            let clusterLabel = labelObj;
                            if (typeof clusterLabel === 'object') {
                                clusterLabel = pickLang(clusterLabel, this.lang) || cluster.id;
                            }
                            
                            deviations.push({
                                clusterId: cluster.id,
                                clusterLabel: clusterLabel,
                                questionId: qid,
                                questionLabel: questionLabel,
                                allPercentage: allPct,
                                segValuePercentage: segValuePct,
                                deviation: deviation,
                                segValueN: segValueData.n,
                                segValueCount: segValueData.count
                            });
                        }
                    }
                }
            }
            
            // sort by deviation (largest positive first, then largest negative)
            deviations.sort((a, b) => Math.abs(b.deviation) - Math.abs(a.deviation));
            
            return deviations;
        },
        
        /**
         * Cluster deviations grouped by question
         * Allows a contextual display in the segment comparison
         */
        getClusterDeviationsGroupedByQuestion(segValueKey) {
            const deviations = this.getClusterDeviationsForSegmentValue(segValueKey);
            
            // group by question
            const grouped = {};
            for (const d of deviations) {
                if (!grouped[d.questionId]) {
                    grouped[d.questionId] = {
                        questionId: d.questionId,
                        questionLabel: d.questionLabel,
                        deviations: []
                    };
                }
                grouped[d.questionId].deviations.push(d);
            }
            
            // convert into an array and sort by the questions with the strongest deviations
            const result = Object.values(grouped);
            result.sort((a, b) => {
                // sort by the maximum deviation of the group
                const maxA = Math.max(...a.deviations.map(d => Math.abs(d.deviation)));
                const maxB = Math.max(...b.deviations.map(d => Math.abs(d.deviation)));
                return maxB - maxA;
            });
            
            // sort by deviation within every group
            for (const group of result) {
                group.deviations.sort((a, b) => Math.abs(b.deviation) - Math.abs(a.deviation));
            }
            
            return result;
        },
        
        /** Text of a multilingual value in the current language (fallback: any). */
        pickLabel(value) {
            if (!value || typeof value !== 'object') return value || '';
            return pickLang(value, this.lang) || '';
        },
        
        /** Dimensions selectable in the segment comparison view. */
        comparisonDimensions() {
            if (this.segmentSubView === 'clusters') return Object.keys(this.segmentClustersData?.dimensions || {});
            if (this.segmentSubView === 'differences') {
                const dims = new Set();
                Object.values(this.segmentInsightsData?.questions || {})
                    .forEach(q => Object.keys(q.dimensions || {}).forEach(d => dims.add(d)));
                return [...dims];
            }
            return Object.keys(this.data?.segments || {});
        },
        
        /** Tested differences (segment_insights.json): questions with listed findings for the dimension. */
        groupDifferenceQuestions() {
            const dim = this.activeComparisonDimension();
            return Object.entries(this.segmentInsightsData?.questions || {})
                .filter(([, q]) => q.dimensions?.[dim]?.findings?.length)
                .map(([id, q]) => ({ id, label: q.label, findings: q.dimensions[dim].findings,
                                     summary: q.dimensions[dim].summary || {} }));
        },
        
        /** Questions tested by the dimension without a listed finding, with their fixed sentence. */
        groupDifferenceStatements() {
            const dim = this.activeComparisonDimension();
            return Object.entries(this.segmentInsightsData?.questions || {})
                .filter(([, q]) => q.dimensions?.[dim] && !q.dimensions[dim].findings?.length)
                .map(([id, q]) => ({ id, label: q.label, statement: q.dimensions[dim].statement }));
        },
        
        groupDifferencesIntro() {
            const points = this.segmentInsightsData?.parameters?.min_difference_points ?? 15;
            return t('group_differences_intro', this.lang).replace('{points}', points);
        },
        
        /** Current comparison dimension, defaulting to the first available one. */
        activeComparisonDimension() {
            const dims = this.comparisonDimensions();
            return dims.includes(this.comparisonDimension) ? this.comparisonDimension : (dims[0] || null);
        },
        
        /** Segment-specific clusters per question for the comparison dimension. */
        comparisonQuestions() {
            const dim = this.activeComparisonDimension();
            return (dim && this.segmentClustersData?.dimensions?.[dim]?.questions) || {};
        },
        
        
        // ============================================
        // URL-HASH-NAVIGATION
        // ============================================
        
        /**
         * Updates the URL hash from the current state
         */
        updateUrlHash() {
            if (this._hashUpdateInProgress) return;
            
            const params = new URLSearchParams();
            
            // always store the viewMode
            params.set('view', this.viewMode);
            
            // language
            params.set('lang', this.lang);
            
            // view-specific parameters
            if (this.viewMode === 'themes' && this.activeTheme) {
                params.set('theme', this.activeTheme);
            }
            
            if (this.viewMode === 'keywords' && this.selectedKeyword) {
                params.set('keyword', this.selectedKeyword);
            }
            
            if (this.viewMode === 'correlations') {
                params.set('correlationView', this.correlationView);
            }
            
            const newHash = '#' + params.toString();
            
            // update only if something changed
            if (window.location.hash !== newHash) {
                history.pushState(null, '', newHash);
            }
        },
        
        /**
         * Restores the state from the URL hash
         */
        restoreStateFromHash() {
            const hash = window.location.hash.slice(1); // remove #
            if (!hash) return;
            
            const params = new URLSearchParams(hash);
            
            // language
            const lang = params.get('lang');
            if (lang && Object.keys(window.i18n || {}).includes(lang)) {
                this.lang = lang;
            }
            
            // View Mode
            const view = params.get('view');
            const validViews = ['home', 'summary', 'questions', 'themes', 'keywords', 'correlations', 'segmentcomparison', 'myprofile'];
            if (view && validViews.includes(view)) {
                this.viewMode = view;
                
                // view-specific parameters
                if (view === 'themes') {
                    const theme = params.get('theme');
                    if (theme && this.themes?.themes?.[theme]) {
                        this.activeTheme = theme;
                    } else if (this.themes?.themes) {
                        // fallback: the first theme
                        const themeIds = Object.keys(this.themes.themes);
                        if (themeIds.length > 0) {
                            themeIds.sort((a, b) => {
                                const orderA = this.themes.themes[a]?.order || 999;
                                const orderB = this.themes.themes[b]?.order || 999;
                                return orderA - orderB;
                            });
                            this.activeTheme = themeIds[0];
                        }
                    }
                }
                
                if (view === 'keywords') {
                    const keyword = params.get('keyword');
                    if (keyword) {
                        this.selectedKeyword = decodeURIComponent(keyword);
                    }
                }
                
                if (view === 'correlations') {
                    const corrView = params.get('correlationView');
                    if (corrView && ['cards', 'heatmap', 'network'].includes(corrView)) {
                        this.correlationView = corrView;
                    }
                }
            }
        }
    };
}

// register Alpine.js on load
document.addEventListener('alpine:init', () => {
    Alpine.data('dashboard', dashboard);
});
