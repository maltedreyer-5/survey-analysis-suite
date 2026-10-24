// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
/**
 * charts.js - Plotly.js charts of the survey dashboard
 * 
 * Supported chart types:
 * - bar charts (horizontal) for single/multiple choice
 * - 100% stacked bars for matrix/Likert questions
 * - cluster charts for free-text analyses
 */

/**
 * Translates an option label into the requested language
 * Supports both formats:
 * - [{key: "...", label: {de, fr, en, it}}]
 * - {de: [...], fr: [...], en: [...]}
 * 
 * @param {Object} question - question object
 * @param {string} optionKey - key/label of the option
 * @param {string} lang - target language
 * @returns {string} translated label
 */
function getOptionLabelFromQuestion(question, optionKey, lang) {
    const options = question?.options;
    if (!options) return optionKey;
    
    // array of {key, label}
    if (Array.isArray(options) && options.length > 0 && options[0]?.key) {
        const option = options.find(o => o.key === optionKey);
        if (option?.label) {
            return pickLang(option.label, lang) || optionKey;
        }
    }
    
    // Fallback: a global translateLabel(label, lang), if a page defines one (none is shipped)
    if (typeof translateLabel === 'function') {
        return translateLabel(optionKey, lang);
    }
    
    return optionKey;
}

// colour palette, consistent with an academic design
const COLORS = {
    // primary colours
    primary: '#2563eb',      // blue
    primaryLight: '#3b82f6',
    primaryDark: '#1d4ed8',
    
    // semantic colours
    success: '#059669',      // green
    warning: '#d97706',      // Orange
    danger: '#dc2626',       // red
    info: '#0891b2',         // Cyan
    
    // neutral colours
    gray: '#6b7280',
    grayLight: '#9ca3af',
    grayDark: '#374151',
    
    // category palette (for up to 10 categories)
    palette: [
        '#2563eb', // blue
        '#059669', // green
        '#d97706', // Orange
        '#dc2626', // red
        '#7c3aed', // violet
        '#db2777', // Pink
        '#0891b2', // Cyan
        '#65a30d', // Lime
        '#ca8a04', // yellow
        '#475569'  // Slate
    ],
    
    // Likert scale (5 steps: very negative -> very positive)
    likertDiverging: [
        '#dc2626', // strongly negative
        '#f97316', // negative
        '#9ca3af', // Neutral
        '#22c55e', // positive
        '#059669'  // strongly positive
    ],
    
    // trend colours (decreased -> same -> increased)
    trend: {
        increased: '#059669',
        unchanged: '#9ca3af',
        decreased: '#dc2626'
    },
    
    // yes/no colours
    yesNo: {
        yes: '#059669',      // green
        no: '#dc2626',       // red
        partial: '#d97706',  // orange (for "partly", "planned", etc.)
        other: '#6b7280'     // grey (fallback)
    },
    
    // ranking/podium colours
    ranking: {
        gold: '#f59e0b',     // 1st place: gold
        silver: '#9ca3af',   // 2nd place: silver
        bronze: '#b45309',   // 3rd place: bronze
        rest: [              // descending greys for the rest
            '#64748b',
            '#78716c', 
            '#94a3b8',
            '#a1a1aa',
            '#a8a29e',
            '#cbd5e1'
        ]
    }
};

/**
 * Keys of a segmentation dimension in the configured order, from the segment
 * metadata of the export (segments[dim].values); empty if not available.
 * @param {Object} segments - this.data.segments
 * @param {string} segmentKey - dimension id
 * @returns {string[]}
 */
function configuredSegmentOrder(segments, segmentKey) {
    const values = segments && segments[segmentKey] && segments[segmentKey].values;
    return Array.isArray(values) ? values.map(v => (v && typeof v === 'object') ? String(v.key) : String(v)) : [];
}

/**
 * Sorts segment data for small multiples
 * - in the configured order of the dimension (export metadata): the first value at the TOP
 * - without that order: by n (largest on top)
 * 
 * IMPORTANT: Plotly shows the FIRST element of the array at the BOTTOM of horizontal bars!
 * So the intended visual order has to be reversed.
 * 
 * @param {Array} segmentValues - array of {key, data} objects
 * @param {Array} segmentLabels - array of labels
 * @param {Array} segmentNs - array of n values
 * @param {string} segmentKey - dimension id
 * @param {Object} segments - segment metadata of the export (for the configured order)
 * @returns {Object} - { sortedIndices, sortedLabels, sortedValues, sortedNs }
 */
function sortSegmentData(segmentValues, segmentLabels, segmentNs, segmentKey, segments) {
    let sortedIndices;
    const order = configuredSegmentOrder(segments, segmentKey);
    
    if (order.length) {
        // configured order: the first value at the TOP of the chart, i.e. LAST in the
        // array (Plotly shows the last on top); unknown keys at the bottom
        const pos = i => { const p = order.indexOf(String(segmentValues[i].key)); return p === -1 ? 999 : p; };
        sortedIndices = segmentValues.map((_, i) => i).sort((a, b) => pos(b) - pos(a));
    } else {
        // no configured order: sort by n
        // largest n on TOP -> the largest n must be the LAST element
        // so: sort ascending (smallest first -> ends up at the bottom)
        sortedIndices = segmentValues.map((_, i) => i).sort((a, b) => segmentNs[a] - segmentNs[b]);
    }
    
    return {
        sortedIndices,
        sortedLabels: sortedIndices.map(i => segmentLabels[i]),
        sortedValues: sortedIndices.map(i => segmentValues[i]),
        sortedNs: sortedIndices.map(i => segmentNs[i])
    };
}

/**
 * Polarity of an option or scale point from a named scale in the configuration
 * (question.options / question.scale entries: {key, label, polarity}); null if not set.
 */
function configuredPolarity(question, key) {
    for (const list of [question?.options, question?.scale]) {
        if (!Array.isArray(list)) continue;
        const entry = list.find(o => o && typeof o === 'object' && String(o.key) === String(key));
        if (entry?.polarity) return entry.polarity;
    }
    return null;
}

/** Trend type of a polarity: positive -> increase, neutral -> unchanged, negative -> decrease. */
const POLARITY_TREND = { positive: 'increase', neutral: 'unchanged', negative: 'decrease' };
const POLARITY_COLORS = { positive: '#22c55e', neutral: '#9ca3af', negative: '#ef4444' };

/**
 * Determines the semantic colour of an answer option
 * "Yes" -> green, "No" -> red, others -> palette
 * 
 * @param {string} key - key of the option
 * @param {string} label - label of the option
 * @param {number} index - index for the fallback colour
 * @returns {string} hex colour code
 */
function getSemanticColor(key, label, index) {
    const keyLower = (key || '').toLowerCase().trim();
    const labelLower = (label || '').toLowerCase().trim();
    
    // === BUDGET DETECTION (increase/same/decrease) ===
    const budgetIncreased = ['erhöht', 'erhoeht', 'augmenté', 'increased', 'gestiegen', 'hausse', 'zugenommen', 'zunahme'];
    const budgetUnchanged = ['gleichgeblieben', 'inchangé', 'unchanged', 'stable', 'resté', 'unverändert', 'unveraendert', 'gleich'];
    const budgetDecreased = ['vermindert', 'diminué', 'decreased', 'gesunken', 'baisse', 'reduziert', 'abgenommen', 'abnahme'];
    
    // budget keywords have a high priority
    for (const pattern of budgetIncreased) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return '#22c55e'; // green
        }
    }
    for (const pattern of budgetDecreased) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return '#ef4444'; // red
        }
    }
    for (const pattern of budgetUnchanged) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return '#9ca3af'; // grey
        }
    }
    
    // === YES/NO DETECTION ===
    // exact yes matches (highest priority)
    const yesExact = ['ja', 'yes', 'oui', 'si', 'true', 'wahr'];
    // exact no matches (highest priority)
    const noExact = ['nein', 'no', 'non', 'false', 'falsch'];
    
    // check exact matches first
    if (yesExact.includes(keyLower) || yesExact.includes(labelLower)) {
        return COLORS.yesNo.yes; // green
    }
    if (noExact.includes(keyLower) || noExact.includes(labelLower)) {
        return COLORS.yesNo.no; // red
    }
    
    // yes variants (multilingual), contained
    const yesPatterns = ['positiv', 'vorhanden', 'eingesetzt', 'aktiv', 'implemented', 'implémenté', 'verfügbar', 'available', 'disponible', 'zugestimmt', 'agreed', 'stimme zu', 'trifft zu', 'applies'];
    // no variants (multilingual), contained
    const noPatterns = ['negativ', 'nicht vorhanden', 'nicht eingesetzt', 'inaktiv', 'not implemented', 'non implémenté', 'nicht verfügbar', 'not available', 'non disponible', 'abgelehnt', 'disagreed', 'stimme nicht', 'trifft nicht', 'does not apply'];
    // partly/planned (orange)
    const partialPatterns = ['teilweise', 'partial', 'partiellement', 'in planung', 'geplant', 'planned', 'planifié', 'in arbeit', 'in progress', 'en cours', 'zum teil', 'manchmal', 'sometimes', 'parfois'];
    
    // check substrings
    for (const pattern of yesPatterns) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return COLORS.yesNo.yes; // green
        }
    }
    
    for (const pattern of noPatterns) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return COLORS.yesNo.no; // red
        }
    }
    
    for (const pattern of partialPatterns) {
        if (keyLower.includes(pattern) || labelLower.includes(pattern)) {
            return COLORS.yesNo.partial; // Orange
        }
    }
    
    // fallback: palette colour by index
    return COLORS.palette[index % COLORS.palette.length];
}

// default layout settings
const DEFAULT_LAYOUT = {
    font: {
        family: '"DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        size: 13,
        color: '#374151'
    },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    margin: { l: 10, r: 20, t: 10, b: 40 },
    hoverlabel: {
        bgcolor: '#1f2937',
        bordercolor: '#1f2937',
        font: { color: '#f9fafb', family: '"DM Sans", sans-serif', size: 12 }
    }
};

// Plotly configuration
const PLOTLY_CONFIG = {
    responsive: true,
    displayModeBar: false,  // switched off completely, no zoom needed
    scrollZoom: false,      // disable scroll zoom
    staticPlot: false,      // hover tooltips stay active
    displaylogo: false
};

// yes/no detection, all language variants
const YES_LABELS = ['ja', 'yes', 'oui', 'si', 'sì'];
const NO_LABELS = ['nein', 'no', 'non'];
const PARTIAL_LABELS = ['teilweise', 'partially', 'partiellement', 'in planung', 'in planning', 'en cours de planification', 'geplant', 'planned', 'prévu'];

/**
 * Checks whether a question is a yes/no question
 * @param {string[]} labels - array of the answer labels
 * @returns {boolean}
 */
function isYesNoQuestion(labels) {
    if (!labels || labels.length === 0) return false;
    
    const normalizedLabels = labels.map(l => l.toLowerCase().trim());
    
    // at least one "yes" and one "no" present
    const hasYes = normalizedLabels.some(l => YES_LABELS.includes(l));
    const hasNo = normalizedLabels.some(l => NO_LABELS.includes(l));
    
    return hasYes && hasNo;
}

/**
 * Colour for yes/no/partly labels
 * @param {string} label - the label
 * @returns {string} hex colour code
 */
function getYesNoColor(label) {
    const l = label.toLowerCase().trim();
    
    // yes = green
    if (YES_LABELS.includes(l)) {
        return COLORS.yesNo.yes;
    }
    
    // no = red
    if (NO_LABELS.includes(l)) {
        return COLORS.yesNo.no;
    }
    
    // partly/planned = orange/yellow
    if (PARTIAL_LABELS.some(p => l.includes(p))) {
        return COLORS.yesNo.partial;
    }
    
    // fallback = grey
    return COLORS.yesNo.other;
}

/**
 * Horizontal bar chart for single-choice and multiple-choice questions
 * @param {HTMLElement} container - DOM container of the chart
 * @param {Object} question - question object with metadata
 * @param {Object} data - result data (n, values, percentages)
 * @param {string} lang - language code
 */
function renderBarChart(container, question, data, lang) {
    if (!data || !data.percentages) {
        renderNoData(container, lang);
        return;
    }
    
    // extract the labels and values
    let labels = Object.keys(data.percentages);
    let percentages = Object.values(data.percentages).map(p => Number(p) || 0);
    let counts = data.values ? Object.values(data.values).map(v => Number(v) || 0) : percentages.map(() => null);
    
    // === SORT HELPERS ===
    
    // detect "Other"
    const isSonstiges = (label) => {
        const lower = label.toLowerCase().trim();
        return lower === 'sonstiges' || lower === 'sonstige' || lower === 'other' || lower === 'autres' || lower === 'autre';
    };
    
    // yes/no order
    const yesNoOrder = {
        'ja': 0, 'yes': 0, 'oui': 0,
        'nein': 1, 'no': 1, 'non': 1,
        'teilweise': 2, 'partially': 2, 'partiellement': 2,
        'in planung': 3, 'planned': 3, 'prévu': 3, 'geplant': 3
    };
    const getYesNoOrder = (label) => yesNoOrder[label.toLowerCase().trim()] ?? 99;
    const isYesNoLike = labels.filter(l => getYesNoOrder(l) < 99).length >= 2;
    
    
    // === APPLY THE SORTING ===
    // IMPORTANT: with horizontal Plotly bars and categoryorder:'trace' the
    // FIRST element is shown at the BOTTOM, so the sort is reversed!
    
    let barColors;
    
    // create the pairs for sorting
    let pairs = labels.map((label, i) => ({
        label, percentage: percentages[i], count: counts[i]
    }));
    
    if (isYesNoLike) {
        // yes/no: yes on top -> yes must be the last element -> sort descending
        pairs.sort((a, b) => getYesNoOrder(b.label) - getYesNoOrder(a.label));
        barColors = pairs.map(p => getYesNoColor(p.label));
        
    } else if (question?.ordinal && Array.isArray(question.options)) {
        // Ordered scale: configured order, first option on top (Plotly draws the first element at the bottom)
        const order = question.options.map(o => o.key);
        const pos = l => { const i = order.indexOf(l); return i === -1 ? order.length : i; };
        pairs.sort((a, b) => pos(b.label) - pos(a.label));
        barColors = pairs.map((p, i) => POLARITY_COLORS[configuredPolarity(question, p.label)] || COLORS.palette[i % COLORS.palette.length]);
        
    } else {
        // default: highest value on top, "Other" at the very bottom
        // the highest must be the last element -> sort ascending by value
        // "Other" must be the first element (shown at the bottom)
        pairs.sort((a, b) => {
            const aIsSonstiges = isSonstiges(a.label);
            const bIsSonstiges = isSonstiges(b.label);
            if (aIsSonstiges && !bIsSonstiges) return -1; // a to the front (bottom)
            if (!aIsSonstiges && bIsSonstiges) return 1;  // b to the front (bottom)
            return a.percentage - b.percentage; // ascending: lowest first (bottom), highest last (top)
        });
        barColors = pairs.map((_, i) => COLORS.palette[i % COLORS.palette.length]);
    }
    
    // extract the sorted values
    labels = pairs.map(p => p.label);
    percentages = pairs.map(p => p.percentage);
    counts = pairs.map(p => p.count);
    
    // translate the labels
    const translatedLabels = labels.map(label => {
        // array of {key, label} objects
        if (question.options && Array.isArray(question.options) && question.options.length > 0 && question.options[0]?.key) {
            const option = question.options.find(o => o.key === label);
            if (option?.label) {
                return pickLang(option.label, lang) || label;
            }
        }
        
        // fallback: translateLabel function
        if (typeof translateLabel === 'function') {
            return translateLabel(label, lang);
        }
        return label;
    });
    
    // yes/no colours if not set yet
    if (!barColors && isYesNoQuestion(labels)) {
        barColors = labels.map(l => getYesNoColor(l));
    }
    
    // use custom colours from question.colors (if present)
    if (!barColors && question.colors && typeof question.colors === 'object') {
        barColors = labels.map(label => question.colors[label] || COLORS.primary);
    }
    
    if (!barColors) {
        barColors = COLORS.palette.slice(0, labels.length);
    }
    
    // longest label for a dynamic margin
    const maxLabelLength = Math.max(...translatedLabels.map(l => l.length));
    const dynamicLeftMargin = Math.min(550, Math.max(200, maxLabelLength * 7));
    
    const trace = {
        type: 'bar',
        orientation: 'h',
        y: translatedLabels,
        x: percentages,
        text: percentages.map((pct, i) => {
            const pctNum = Number(pct) || 0;
            const countStr = counts[i] !== null ? ` (n=${counts[i]})` : '';
            return `${pctNum.toFixed(1)}%${countStr}`;
        }),
        textposition: 'outside',
        textfont: { size: 13, color: '#374151' },
        marker: {
            color: barColors,
            line: { color: 'rgba(0,0,0,0.1)', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>%{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    // dynamic height
    const barHeight = 45;
    const chartHeight = Math.max(200, labels.length * barHeight + 60);
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        margin: { l: dynamicLeftMargin, r: 80, t: 10, b: 40 },
        xaxis: {
            
            range: [0, Math.min(100, Math.max(...percentages) * 1.25)],
            ticksuffix: '%',
            gridcolor: '#e5e7eb',
            gridwidth: 1,
            zeroline: true,
            zerolinecolor: '#d1d5db'
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 },
            ticksuffix: '  ',
            categoryorder: 'trace'  // sorting controlled by JS
        },
        bargap: 0.3
    };
    
    Plotly.newPlot(container, [trace], layout, PLOTLY_CONFIG);
}

/**
 * Comparison bar chart: overall vs. filter (grouped bar chart)
 * Shows two bars side by side: the overall data and the filtered data
 * @param {HTMLElement} container - DOM container of the chart
 * @param {Object} question - question object with metadata
 * @param {Object} allData - overall result data (n, values, percentages)
 * @param {Object} filteredData - filtered result data (n, values, percentages)
 * @param {string} lang - language code
 * @param {string} filterLabel - description of the active filter
 */
function renderComparisonChart(container, question, allData, filteredData, lang, filterLabel = null) {
    filterLabel = filterLabel || t('selection', lang);
    if (!allData || !allData.percentages) {
        renderNoData(container, lang);
        return;
    }
    
    // labels from the overall data (as the reference)
    let labels = Object.keys(allData.percentages);
    
    // detect "Other" for the sorting
    const isSonstiges = (label) => {
        const lower = label.toLowerCase().trim();
        return lower === 'sonstiges' || lower === 'sonstige' || lower === 'other' || lower === 'autres' || lower === 'autre';
    };
    
    // create and sort the pairs (highest value on top, "Other" at the bottom)
    let pairs = labels.map(label => ({
        label,
        allPct: Number(allData.percentages[label]) || 0,
        filteredPct: filteredData?.percentages ? (Number(filteredData.percentages[label]) || 0) : 0,
        allCount: allData.values ? (Number(allData.values[label]) || 0) : null,
        filteredCount: filteredData?.values ? (Number(filteredData.values[label]) || 0) : null
    }));
    
    // sort: highest overall value on top, "Other" at the very bottom
    // with horizontal Plotly bars the first element is shown at the bottom
    pairs.sort((a, b) => {
        const aIsSonstiges = isSonstiges(a.label);
        const bIsSonstiges = isSonstiges(b.label);
        if (aIsSonstiges && !bIsSonstiges) return -1;
        if (!aIsSonstiges && bIsSonstiges) return 1;
        return a.allPct - b.allPct; // ascending: lowest at the bottom
    });
    
    // extract the sorted values
    const sortedLabels = pairs.map(p => p.label);
    const allPercentages = pairs.map(p => p.allPct);
    const filteredPercentages = pairs.map(p => p.filteredPct);
    const allCounts = pairs.map(p => p.allCount);
    const filteredCounts = pairs.map(p => p.filteredCount);
    
    // translate the labels
    const translatedLabels = sortedLabels.map(label => {
        // try the newer function
        const translated = getOptionLabelFromQuestion(question, label, lang);
        if (translated !== label) return translated;
        
        // fallback: translateLabel
        if (typeof translateLabel === 'function') {
            return translateLabel(label, lang);
        }
        return label;
    });
    
    // translations for the legend
    const legendAll = {
        de: `Gesamt (n=${allData.n || 0})`,
        fr: `Total (n=${allData.n || 0})`,
        en: `Total (n=${allData.n || 0})`,
        it: `Totale (n=${allData.n || 0})`
    };
    const legendFiltered = {
        de: `${filterLabel} (n=${filteredData?.n || 0})`,
        fr: `${filterLabel} (n=${filteredData?.n || 0})`,
        en: `${filterLabel} (n=${filteredData?.n || 0})`,
        it: `${filterLabel} (n=${filteredData?.n || 0})`
    };
    
    // longest label for a dynamic margin
    const maxLabelLength = Math.max(...translatedLabels.map(l => l.length));
    const dynamicLeftMargin = Math.min(550, Math.max(200, maxLabelLength * 7));
    
    // trace 1: overall data (grey)
    const traceAll = {
        name: pickLang(legendAll, lang),
        type: 'bar',
        orientation: 'h',
        y: translatedLabels,
        x: allPercentages,
        text: allPercentages.map((pct, i) => `${pct.toFixed(1)}%`),
        textposition: 'outside',
        textfont: { size: 13, color: '#64748b' },
        marker: {
            color: '#cbd5e1',  // Slate-300
            line: { color: '#94a3b8', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>' + t('total_label', lang) + ': %{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    // trace 2: filtered data (brand colour)
    const traceFiltered = {
        name: pickLang(legendFiltered, lang),
        type: 'bar',
        orientation: 'h',
        y: translatedLabels,
        x: filteredPercentages,
        text: filteredPercentages.map((pct, i) => `${pct.toFixed(1)}%`),
        textposition: 'outside',
        textfont: { size: 13, color: '#0369a1' },
        marker: {
            color: '#0ea5e9',  // Sky-500 (Brand)
            line: { color: '#0284c7', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>' + filterLabel + ': %{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    // dynamic height (a little more room for grouped bars)
    const barHeight = 55;
    const chartHeight = Math.max(250, sortedLabels.length * barHeight + 100);
    
    // determine the maximum of the x axis
    const maxPct = Math.max(...allPercentages, ...filteredPercentages);
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        margin: { l: dynamicLeftMargin, r: 80, t: 40, b: 40 },
        barmode: 'group',
        bargap: 0.2,
        bargroupgap: 0.1,
        showlegend: true,
        legend: {
            orientation: 'h',
            y: 1.08,
            x: 0.5,
            xanchor: 'center',
            font: { size: 12 },
            bgcolor: 'rgba(255,255,255,0.8)',
            bordercolor: '#e2e8f0',
            borderwidth: 1
        },
        xaxis: {
            
            range: [0, Math.min(100, maxPct * 1.3)],
            ticksuffix: '%',
            gridcolor: '#e5e7eb',
            gridwidth: 1,
            zeroline: true,
            zerolinecolor: '#d1d5db'
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 },
            ticksuffix: '  ',
            categoryorder: 'trace'
        }
    };
    
    Plotly.newPlot(container, [traceAll, traceFiltered], layout, PLOTLY_CONFIG);
}

/**
 * Ranking chart with medals and places
 * @param {HTMLElement} container - DOM container of the chart
 * @param {Object} question - question object with metadata
 * @param {Object} data - result data (n, values, percentages)
 * @param {string} lang - language code
 */
function renderRankingChart(container, question, data, lang) {
    if (!data || !data.percentages) {
        renderNoData(container, lang);
        return;
    }
    
    // extract the labels and values and sort by percentage (with number conversion)
    // highest value first (for the right order in the chart)
    const sortedEntries = Object.entries(data.percentages)
        .map(([label, pct]) => [label, Number(pct) || 0])
        .sort((a, b) => b[1] - a[1]);
    
    const labels = sortedEntries.map(([label]) => label);
    const percentages = sortedEntries.map(([, pct]) => pct);
    
    // in rankings 'values' are weighted scores, NOT counts!
    // so no n values are shown at the bars
    
    // translate the labels, using question.options if available
    const translatedLabels = labels.map(label => {
        // first look for the key in question.options
        if (question.options && Array.isArray(question.options)) {
            const option = question.options.find(o => o.key === label);
            if (option?.label) {
                return pickLang(option.label, lang) || label;
            }
        }
        // fallback: translateLabel function
        if (typeof translateLabel === 'function') {
            return translateLabel(label, lang);
        }
        // last fallback: format the key (underscores as spaces, capitalised)
        return label.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    });
    
    // medal emojis
    const medals = ['🥇', '🥈', '🥉'];
    
    // format the labels with the place (without artificial shortening)
    const rankedLabels = translatedLabels.map((label, idx) => {
        const rank = idx + 1;
        const medal = idx < 3 ? medals[idx] : '    '; // spaces for the alignment
        const rankNum = rank <= 3 ? `${rank}.` : `${rank}. `;
        return `${medal} ${rankNum}  ${label}`;
    });
    
    // dynamic margin from the label length, calculated generously
    const maxLabelLength = Math.max(...rankedLabels.map(l => l.length));
    const dynamicLeftMargin = Math.min(550, Math.max(280, maxLabelLength * 7));
    
    // ranking colours: gold, silver, bronze, then descending greys
    const barColors = labels.map((_, idx) => {
        if (idx === 0) return COLORS.ranking.gold;
        if (idx === 1) return COLORS.ranking.silver;
        if (idx === 2) return COLORS.ranking.bronze;
        return COLORS.ranking.rest[(idx - 3) % COLORS.ranking.rest.length];
    });
    
    // IMPORTANT: with horizontal Plotly bars and categoryorder:'trace' the
    // FIRST element is shown at the BOTTOM, so the arrays are reversed!
    const reversedLabels = [...rankedLabels].reverse();
    const reversedPercentages = [...percentages].reverse();
    const reversedColors = [...barColors].reverse();
    
    const trace = {
        type: 'bar',
        orientation: 'h',
        y: reversedLabels,
        x: reversedPercentages,
        text: reversedPercentages.map((pct) => {
            const pctNum = Number(pct) || 0;
            // no n values in rankings (values are weighted scores)
            return `${pctNum.toFixed(1)}%`;
        }),
        textposition: 'outside',
        textfont: { size: 13, color: '#374151' },
        marker: {
            color: reversedColors,
            line: { color: 'rgba(0,0,0,0.15)', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>%{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    // dynamic height from the number of bars
    const barHeight = 50; // a little more room for the medals
    const chartHeight = Math.max(250, labels.length * barHeight + 80);
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        margin: { l: dynamicLeftMargin, r: 80, t: 10, b: 40 },
        xaxis: {
            
            range: [0, Math.min(100, Math.max(...percentages) * 1.25)],
            ticksuffix: '%',
            gridcolor: '#e5e7eb',
            gridwidth: 1,
            zeroline: true,
            zerolinecolor: '#d1d5db'
        },
        yaxis: {
            automargin: true,
            tickfont: { 
                size: 13,
                family: 'system-ui, -apple-system, sans-serif'
            },
            ticksuffix: '  ', // a small gap between label and bar
            categoryorder: 'trace'
        },
        bargap: 0.25
    };
    
    Plotly.newPlot(container, [trace], layout, PLOTLY_CONFIG);
    
    // show the score explanation (if any)
    // the explanation is searched and replaced in the parent container (not in the Plotly container itself)
    const parentContainer = container.parentElement;
    if (parentContainer) {
        const existingExplanation = parentContainer.querySelector('.score-explanation');
        if (existingExplanation) {
            existingExplanation.remove();
        }
        
        if (question.score_explanation) {
            const explanation = pickLang(question.score_explanation, lang) || question.score_explanation['en'];
            if (explanation) {
                const hintLabel = t('calc_note', lang);
                const explanationDiv = document.createElement('div');
                explanationDiv.className = 'score-explanation mt-3 p-3 bg-slate-50 rounded-lg border border-slate-200 text-xs text-slate-600';
                explanationDiv.innerHTML = `<span class="font-medium text-slate-700">ℹ️ ${escapeHtml(hintLabel)}</span> ${escapeHtml(explanation)}`;
                parentContainer.appendChild(explanationDiv);
            }
        }
    }
}

/**
 * 100% stacked bar chart for matrix/Likert questions
 * @param {HTMLElement} container - DOM container
 * @param {Object} question - question object
 * @param {Object} data - result data with a by_row or percentages structure
 * @param {string} lang - language code
 */
function renderStackedBarChart(container, question, data, lang) {
    // supports several data formats:
    // format A: by_row: { "Row1": { percentages: { "Scale1": x, "Scale2": y }, label: {de, fr, en} } }
    // format B: items: { "Row1": { percentages: { "Scale1": x, "Scale2": y }, label: {de, fr, en} } }
    // format C: percentages: { "Scale1": { "Row1": x, "Row2": y }, "Scale2": {...} }
    
    let rows, scale, getPercentage, getRowLabel;
    
    // format A/B: by_row or items structure
    const itemsData = data?.items || data?.by_row;
    if (itemsData && typeof itemsData === 'object') {
        rows = Object.keys(itemsData);
        const firstRow = Object.values(itemsData)[0];
        // use distribution or percentages
        const pctKey = firstRow?.percentages ? 'percentages' : (firstRow?.distribution ? 'distribution' : null);
        if (pctKey && firstRow[pctKey]) {
            scale = Object.keys(firstRow[pctKey]);
        } else {
            scale = [];
        }
        getPercentage = (row, scaleItem) => {
            const rowData = itemsData[row];
            return rowData?.percentages?.[scaleItem] || rowData?.distribution?.[scaleItem] || 0;
        };
        // label function: the embedded label or question.rows/items
        getRowLabel = (row) => {
            // 1. the label directly in the data
            if (pickLang(itemsData[row]?.label, lang)) return pickLang(itemsData[row].label, lang);
            // 2. Label in question.items
            if (pickLang(question.items?.[row]?.label, lang)) return pickLang(question.items[row].label, lang);
            // 3. Label in question.rows
            if (pickLang(question.rows?.[row], lang)) return pickLang(question.rows[row], lang);
            // 4. fallback: translateLabel or the original
            if (typeof translateLabel === 'function') return translateLabel(row, lang);
            return row;
        };
        
    } else if (data && data.percentages && typeof data.percentages === 'object') {
        // format C: percentages with scale keys
        scale = Object.keys(data.percentages).filter(k => typeof data.percentages[k] === 'object');
        if (scale.length === 0) {
            renderNoData(container, lang);
            return;
        }
        rows = Object.keys(data.percentages[scale[0]] || {});
        getPercentage = (row, scaleItem) => Number(data.percentages[scaleItem]?.[row]) || 0;
        getRowLabel = (row) => {
            if (question.rows?.[row]?.[lang]) return question.rows[row][lang];
            if (typeof translateLabel === 'function') return translateLabel(row, lang);
            return row;
        };
        
    } else {
        renderNoData(container, lang);
        return;
    }
    
    if (rows.length === 0 || scale.length === 0) {
        renderNoData(container, lang);
        return;
    }
    
    // override the scale from the question if present;
    // may be an array of strings OR an array of {key, label} objects
    let scaleLabels = {}; // Mapping key -> translated label
    
    if (question.scale && Array.isArray(question.scale)) {
        // are these objects with key/label?
        if (question.scale.length > 0 && question.scale[0]?.key) {
            // [{key: 'zunahme', label: {de: 'Zunahme', ...}}, ...]
            scale = question.scale.map(s => s.key);
            question.scale.forEach(s => {
                const label = pickLang(s.label, lang) || s.key;
                scaleLabels[s.key] = label;
            });
        }
        // otherwise question.scale is an array of strings already
    }
    
    // translate the scale items
    const translatedScale = scale.map(item => {
        // first: is there a label in scaleLabels?
        if (scaleLabels[item]) {
            return scaleLabels[item];
        }
        // then try translateLabel
        if (typeof translateLabel === 'function') {
            return translateLabel(item, lang);
        }
        return item;
    });
    
    // translate the rows with getRowLabel
    const rowTranslations = {};
    rows.forEach(row => {
        rowTranslations[row] = getRowLabel(row);
    });
    
    // trend scales: sort the rows by the "increase" value (highest on top);
    // Plotly draws the bars from the bottom up, so the lowest value comes first
    const trendKeywords = {
        increase: ['zunahme', 'augmentation', 'increase', 'verstärkt', 'verstaerkt', 'renforcé', 'strengthened', 'erhöht', 'erhoeht'],
        unchanged: ['unverändert', 'unveraendert', 'inchangé', 'unchanged', 'nicht verändert', 'nicht veraendert', 'no change', 'pas de changement', 'gleichgeblieben'],
        decrease: ['abnahme', 'diminution', 'decrease', 'verringert', 'réduit', 'reduced', 'gemindert', 'vermindert']
    };
    
    const getTrendType = (item) => {
        // the polarity configured for the scale point decides; the keywords only
        // serve surveys without polarity
        const polarity = configuredPolarity(question, item);
        if (polarity) return POLARITY_TREND[polarity] || null;
        const lower = item.toLowerCase();
        if (trendKeywords.increase.some(k => lower.includes(k))) return 'increase';
        if (trendKeywords.decrease.some(k => lower.includes(k))) return 'decrease';
        if (trendKeywords.unchanged.some(k => lower.includes(k))) return 'unchanged';
        return null;
    };
    
    const isTrendScale = (scaleItems) => {
        const types = scaleItems.map(getTrendType);
        return types.includes('increase') && types.includes('decrease');
    };
    
    // find the "increase" scale key for the sorting
    let sortScaleKey = null;
    if (isTrendScale(scale)) {
        sortScaleKey = scale.find(s => getTrendType(s) === 'increase');
    }
    
    // sort the rows by the main value (increase)
    // with categoryarray the first element is shown at the TOP
    // so sort descending: highest value first (top)
    let sortedRows = [...rows];
    if (sortScaleKey) {
        sortedRows.sort((a, b) => {
            const valA = getPercentage(a, sortScaleKey);
            const valB = getPercentage(b, sortScaleKey);
            return valB - valA; // descending: highest first (top), lowest last (bottom)
        });
    }
    
    // translated rows in the sorted order
    const translatedRows = sortedRows.map(row => rowTranslations[row]);
    
    // dynamic margin from the label length, generous
    const maxLabelLength = Math.max(...translatedRows.map(l => l.length));
    const dynamicLeftMargin = Math.min(500, Math.max(200, maxLabelLength * 7));
    
    // does a scale item have values > 0 over all rows?
    const hasAnyValue = (scaleItem) => {
        return sortedRows.some(r => getPercentage(r, scaleItem) > 0);
    };
    
    // determine the colours and the order
    let orderedScale = [...scale];
    let orderedTranslatedScale = [...translatedScale];
    let colors;
    
    if (isTrendScale(scale)) {
        // trend scale: sort by increase -> unchanged -> decrease
        const trendOrder = ['increase', 'unchanged', 'decrease'];
        const trendColors = {
            increase: '#22c55e',  // green
            unchanged: '#9ca3af', // grey
            decrease: '#ef4444'   // red
        };
        
        // create the pairs (originalScale, translatedScale, trendType)
        const scalePairs = scale.map((s, i) => ({
            original: s,
            translated: translatedScale[i],
            type: getTrendType(s),
            hasData: hasAnyValue(s)
        }));
        
        // sort by the trend order, only items with a detected type AND data
        const sortedPairs = scalePairs
            .filter(p => p.type !== null && p.hasData)
            .sort((a, b) => trendOrder.indexOf(a.type) - trendOrder.indexOf(b.type));
        
        orderedScale = sortedPairs.map(p => p.original);
        orderedTranslatedScale = sortedPairs.map(p => p.translated);
        colors = sortedPairs.map(p => trendColors[p.type]);
    } else {
        // default colours of the Likert scale; items without data are filtered here too
        const filteredIndices = scale.map((s, i) => ({ scale: s, translated: translatedScale[i], idx: i, hasData: hasAnyValue(s) }))
            .filter(item => item.hasData);
        
        orderedScale = filteredIndices.map(item => item.scale);
        orderedTranslatedScale = filteredIndices.map(item => item.translated);
        
        const baseColors = question.color_scheme === 'diverging' 
            ? COLORS.likertDiverging 
            : COLORS.palette;
        colors = filteredIndices.map(item => baseColors[item.idx % baseColors.length]);
    }
    
    // no scale items left: fallback
    if (orderedScale.length === 0) {
        orderedScale = scale;
        orderedTranslatedScale = translatedScale;
        colors = COLORS.palette.slice(0, scale.length);
    }
    
    // create the traces for every scale value
    const traces = orderedTranslatedScale.map((scaleItem, idx) => ({
        type: 'bar',
        orientation: 'h',
        name: scaleItem,
        y: translatedRows,
        x: sortedRows.map(r => getPercentage(r, orderedScale[idx])), // sorted rows for the data lookup
        text: sortedRows.map(r => {
            const pct = getPercentage(r, orderedScale[idx]);
            return pct >= 8 ? `${pct.toFixed(0)}%` : '';
        }),
        textposition: 'inside',
        textfont: { size: 13, color: '#ffffff' },
        marker: {
            color: colors[idx % colors.length],
            line: { color: 'rgba(255,255,255,0.5)', width: 1 }
        },
        hovertemplate: `<b>${scaleItem}</b><br>%{y}: %{x:.1f}%<extra></extra>`
    }));
    
    // dynamic height
    const rowHeight = 40;
    const chartHeight = Math.max(250, sortedRows.length * rowHeight + 100);
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        barmode: 'stack',
        margin: { l: dynamicLeftMargin, r: 20, t: 10, b: 60 },
        xaxis: {
            
            range: [0, 100],
            ticksuffix: '%',
            gridcolor: '#e5e7eb',
            dtick: 25
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 },
            ticksuffix: '  ', // a small gap between label and bar
            // explicit order of the y axis: translatedRows is sorted already
            // (lowest increase value first = bottom, highest last = top)
            categoryorder: 'array',
            categoryarray: translatedRows
        },
        legend: {
            orientation: 'h',
            y: -0.15,
            x: 0.5,
            xanchor: 'center',
            font: { size: 12 },
            traceorder: 'normal'  // legend in the order of the traces
        },
        bargap: 0.25
    };
    
    Plotly.newPlot(container, traces, layout, PLOTLY_CONFIG);
}

/**
 * Cluster chart for free-text analyses
 * @param {HTMLElement} container - DOM container
 * @param {Object} question - question object with the cluster definitions
 * @param {Object} data - result data with cluster_percentages
 * @param {string} lang - language code
 */
function renderClusterChart(container, question, data, lang) {
    const clusters = question.clusters || [];
    
    // Clusters carry results.all.percentage; without cluster definitions
    // (segment-specific clustering) the result's cluster_percentages count
    const useNewFormat = clusters.length > 0 && clusters[0]?.results?.all?.percentage !== undefined;
    
    // Segment-specific clusters are an independent clustering of one segment:
    // render them from their own result, not from the global cluster list.
    if (data && data.clustering === 'segment' && clusters.length > 0) {
        return renderClusterChart(container, { ...question, clusters: [] }, data, lang);
    }
    // Global clusters counted within the active segment (keyed by cluster id)
    const segPct = data?.cluster_percentages_by_id;
    const segCount = data?.cluster_counts_by_id;
    
    if (!useNewFormat && (!data || !data.cluster_percentages)) {
        renderNoData(container, lang);
        return;
    }
    
    let clusterData = [];
    
    if (useNewFormat) {
        // data directly from the cluster definitions
        clusterData = clusters
            .map(c => {
                const labelLang = pickLang(c.label, lang) || c.id;
                const allResults = c.results?.all || {};
                
                return {
                    id: c.id,
                    label: labelLang,
                    percentage: segPct ? (segPct[c.id] ?? 0) : (allResults.percentage || 0),
                    count: segCount ? (segCount[c.id] ?? 0) : (allResults.count || 0),
                    description: c.description ? (pickLang(c.description, lang)) : null
                };
            })
            .filter(c => c.percentage > 0)
            .sort((a, b) => b.percentage - a.percentage);
    } else {
        // no cluster definitions: use the keys directly
        clusterData = Object.entries(data.cluster_percentages)
            .map(([label, percentage]) => {
                let translatedLabel = label;
                if (typeof translateLabel === 'function') {
                    translatedLabel = translateLabel(label, lang);
                }
                return {
                    id: label,
                    label: translatedLabel,
                    percentage: percentage,
                    count: data.cluster_counts ? data.cluster_counts[label] : null,
                    description: null
                };
            })
            .filter(c => c.percentage > 0)
            .sort((a, b) => b.percentage - a.percentage);
    }
    
    if (clusterData.length === 0) {
        renderNoData(container, lang);
        return;
    }
    
    // use the labels without shortening
    const labels = clusterData.map(c => c.label);
    
    // dynamic margin from the label length, generous
    const maxLabelLength = Math.max(...labels.map(l => l.length));
    const dynamicLeftMargin = Math.min(550, Math.max(200, maxLabelLength * 7));
    
    const trace = {
        type: 'bar',
        orientation: 'h',
        y: labels,
        x: clusterData.map(c => Number(c.percentage) || 0),
        text: clusterData.map(c => {
            const pct = Number(c.percentage) || 0;
            const countStr = c.count !== null ? ` (n=${c.count})` : '';
            return `${pct.toFixed(1)}%${countStr}`;
        }),
        textposition: 'outside',
        textfont: { size: 13, color: '#374151' },
        marker: {
            color: COLORS.palette.slice(0, clusterData.length),
            line: { color: 'rgba(0,0,0,0.1)', width: 1 }
        },
        hovertemplate: clusterData.map(c => {
            let template = `<b>${c.label}</b><br>%{x:.1f}%`;
            if (c.description) {
                // shorten the description for the hover
                const shortDesc = c.description.length > 100 
                    ? c.description.substring(0, 97) + '...' 
                    : c.description;
                template += `<br><i style="font-size: 10px">${shortDesc}</i>`;
            }
            return template + '<extra></extra>';
        }),
        cliponaxis: false
    };
    
    const barHeight = 45;
    const chartHeight = Math.max(200, clusterData.length * barHeight + 60);
    
    const xAxisTitle = {
        de: '% der Nennungen',
        fr: '% des mentions',
        it: '% delle menzioni',
        en: '% of mentions'
    };
    
    const maxPct = Math.max(...clusterData.map(c => Number(c.percentage) || 0));
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        margin: { l: dynamicLeftMargin, r: 90, t: 10, b: 40 },
        xaxis: {
            title: { text: pickLang(xAxisTitle, lang), font: { size: 12 } },
            range: [0, Math.min(100, maxPct * 1.35)],
            ticksuffix: '%',
            gridcolor: '#e5e7eb'
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 },
            ticksuffix: '  ',
            categoryorder: 'total ascending'  // lowest first (bottom), highest last (top)
        },
        bargap: 0.3
    };
    
    Plotly.newPlot(container, [trace], layout, PLOTLY_CONFIG);
}

/**
 * Shows the "no data" message
 * @param {HTMLElement} container - DOM container
 * @param {string} lang - language code
 */
/** Privacy threshold of the loaded survey (set by app.js from metadata.min_n). */
function surveyMinN() {
    // the value set by app.js; a global function of the same name would be overwritten by it
    return (typeof window !== 'undefined' && window.surveyMinNValue) || 5;
}

/** Escape text for insertion into HTML strings. */
/** A value as part of an HTML id or of a JS string in an attribute: only [A-Za-z0-9_-]. */
function safeId(value) {
    return String(value ?? '').replace(/[^A-Za-z0-9_-]/g, '_');
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function renderNoData(container, lang, minN = 5) {
    const messages = {
        de: `Nicht genügend Daten für eine Auswertung (n < ${minN})`,
        fr: `Données insuffisantes pour l'analyse (n < ${minN})`,
        en: `Insufficient data for analysis (n < ${minN})`,
        it: `Dati insufficienti per l'analisi (n < ${minN})`
    };
    
    container.innerHTML = `
        <div class="flex flex-col items-center justify-center h-full py-12 text-gray-400">
            <svg class="w-16 h-16 mb-4 opacity-50" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" 
                      d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>
            </svg>
            <p class="text-sm">${escapeHtml(pickLang(messages, lang))}</p>
        </div>
    `;
}

/**
 * Exports a chart as an image
 * @param {string} containerId - id of the chart container
 * @param {string} filename - file name without extension
 * @param {string} format - 'png' or 'svg'
 */
function exportChart(containerId, filename, format = 'png') {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    Plotly.downloadImage(container, {
        format: format,
        filename: filename,
        height: 600,
        width: 1000,
        scale: 2
    });
}

/**
 * Renders all charts in a container again (e.g. after a resize)
 * @param {string} containerSelector - CSS selector of the container
 */
function resizeAllCharts(containerSelector = '[id^="chart-"]') {
    document.querySelectorAll(containerSelector).forEach(container => {
        if (container._fullData) {
            Plotly.Plots.resize(container);
        }
    });
}

/**
 * Renders a cooperation matrix as a stacked bar chart
 */
function renderCooperationMatrixChart(container, question, resultData, lang = 'de') {
    if (!container) {
        console.error('Container nicht gefunden');
        return;
    }
    
    // is there data? (structure with by_row)
    const byRow = resultData?.by_row;
    
    if (!byRow || Object.keys(byRow).length === 0) {
        container.innerHTML = '<div class="text-slate-400 text-center py-8">' + t('no_data_available', lang) + '</div>';
        return;
    }
    
    // extract the partners (rows) and the importance levels (columns/stacks)
    const partners = Object.keys(byRow);
    const firstPartner = byRow[partners[0]];
    const importanceLevels = Object.keys(firstPartner?.percentages || {});
    
    if (importanceLevels.length === 0) {
        container.innerHTML = '<div class="text-slate-400 text-center py-8">' + t('no_data_available', lang) + '</div>';
        return;
    }
    
    // translate the partner labels
    const translatedPartners = partners.map(p => 
        typeof translateLabel === 'function' ? translateLabel(p, lang) : p
    );
    
    // colours of the importance levels: grey -> light blue -> medium blue -> deep blue
    // based on the actual keys in the data
    const levelColors = {
        // no cooperation -> grey
        'keine_zusammenarbeit': '#d1d5db',
        'Keine Zusammenarbeit': '#d1d5db',
        'No cooperation': '#d1d5db',
        'Pas de coopération': '#d1d5db',
        
        // low importance -> light blue
        'von_geringer_bedeutung': '#93c5fd',
        'Von geringer Bedeutung': '#93c5fd',
        'Of minor importance': '#93c5fd',
        'De faible importance': '#93c5fd',
        
        // important -> medium blue
        'bedeutsam': '#3b82f6',
        'Bedeutsam': '#3b82f6',
        'Significant': '#3b82f6',
        'Significatif': '#3b82f6',
        
        // central/highly strategic -> deep blue
        'zentral_fuer_unsere_it_strategie': '#1e40af',
        'Zentral für unsere IT-Strategie': '#1e40af',
        'Von hoher strategischer Bedeutung': '#1e40af',
        'Central to our IT strategy': '#1e40af',
        'Of high strategic importance': '#1e40af',
        'Centrale pour notre stratégie IT': '#1e40af'
    };
    
    // fallback colour scale for unknown labels (grey -> blue gradient)
    const fallbackColors = ['#d1d5db', '#93c5fd', '#3b82f6', '#1e40af', '#1e3a8a'];
    
    // create the traces for every importance level
    const traces = importanceLevels.map((level, idx) => {
        const translatedLevel = typeof translateLabel === 'function' ? translateLabel(level, lang) : level;
        return {
            name: translatedLevel,
            type: 'bar',
            orientation: 'h',
            y: translatedPartners,
            x: partners.map(partner => byRow[partner]?.percentages?.[level] || 0),
            marker: {
                color: levelColors[level] || fallbackColors[idx % fallbackColors.length]
            },
            hovertemplate: '%{y}<br>' + translatedLevel + ': %{x:.1f}%<extra></extra>'
        };
    });
    
    const layout = {
        barmode: 'stack',
        showlegend: true,
        legend: {
            orientation: 'h',
            y: -0.25,
            x: 0.5,
            xanchor: 'center',
            font: { size: 12 }
        },
        margin: { l: 200, r: 30, t: 20, b: 80 },
        xaxis: {
            range: [0, 100],
            ticksuffix: '%'
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 }
        },
        height: Math.max(300, partners.length * 45 + 120),
        font: {
            family: 'Inter, system-ui, sans-serif'
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)'
    };
    
    const config = {
        responsive: true,
        displayModeBar: false
    };
    
    Plotly.newPlot(container, traces, layout, config);
}

/**
 * Free-text cluster comparison chart for the "own selection" view
 * Compares the cluster frequencies: overall vs. selection
 * 
 * @param {HTMLElement} container - DOM container
 * @param {Object} clusterData - cluster object with segmented results
 * @param {Object} appliedFilters - active filters per dimension {<dimension>: [values]}
 * @param {string} lang - language code
 * @param {string} filterLabel - label of the filter group
 */
function renderClusterComparisonChart(container, clusterData, appliedFilters, lang, filterLabel = null) {
    filterLabel = filterLabel || t('selection', lang);
    if (!clusterData || !clusterData.clusters || clusterData.clusters.length === 0) {
        renderNoData(container, lang);
        return;
    }
    
    // helper: aggregate the cluster data of several segments
    function aggregateClusterResults(cluster, filters) {
        const results = cluster.results;
        if (!results) return { count: 0, n: 0, percentage: 0 };
        
        // no filters -> overall data
        if (Object.values(filters || {}).every(v => !Array.isArray(v) || v.length === 0)) {
            const all = results.all || {};
            return {
                count: all.count || 0,
                n: all.n || 0,
                percentage: all.percentage || 0
            };
        }
        
        // create all filter combinations
        // Every combination of the selected values of all dimensions
        let combos = [[]];
        for (const [key, selected] of Object.entries(filters)) {
            if (!Array.isArray(selected) || selected.length === 0) continue;
            combos = combos.flatMap(c => selected.map(value => [...c, { key, value }]));
        }
        
        let totalCount = 0;
        let totalN = 0;
        const seenKeys = new Set();
        
        for (const segments of combos) {
            if (segments.length === 0) continue;
            
            // Keys as in the export: dimensions sorted, values in that order
            segments.sort((a, b) => a.key.localeCompare(b.key));
            const resultKey = 'by_' + segments.map(s => s.key).join('_');
            const valueKey = segments.map(s => s.value).join('|');
            
            // No fallback to a single dimension: a missing combination is
            // below the threshold (or not configured) and must not be
            // replaced by another group's numbers.
            const data = results[resultKey]?.[valueKey];
            
            if (data && !seenKeys.has(valueKey)) {
                seenKeys.add(valueKey);
                totalCount += data.count || 0;
                totalN += data.n || 0;
            }
        }
        
        return {
            count: totalCount,
            n: totalN,
            percentage: totalN > 0 ? (totalCount / totalN) * 100 : 0
        };
    }
    
    // collect the data of all clusters
    const chartData = clusterData.clusters.map(cluster => {
        const label = pickLang(cluster.label, lang) || cluster.id;
        const allResults = cluster.results?.all || { count: 0, n: 0, percentage: 0 };
        const filteredResults = aggregateClusterResults(cluster, appliedFilters);
        
        return {
            id: cluster.id,
            label: label,
            allPct: allResults.percentage || 0,
            allCount: allResults.count || 0,
            allN: allResults.n || 0,
            filteredPct: filteredResults.percentage,
            filteredCount: filteredResults.count,
            filteredN: filteredResults.n,
            // compute the difference for the trend display
            diff: filteredResults.percentage - (allResults.percentage || 0)
        };
    })
    .filter(c => c.allPct > 0 || c.filteredPct > 0)
    .sort((a, b) => b.allPct - a.allPct);  // sort by the overall percentage
    
    if (chartData.length === 0) {
        renderNoData(container, lang);
        return;
    }
    
    // for Plotly: reverse the order (the first element is shown at the bottom)
    chartData.reverse();
    
    const labels = chartData.map(c => c.label);
    const allPercentages = chartData.map(c => c.allPct);
    const filteredPercentages = chartData.map(c => c.filteredPct);
    
    // dynamic margin
    const maxLabelLength = Math.max(...labels.map(l => l.length));
    const dynamicLeftMargin = Math.min(550, Math.max(200, maxLabelLength * 7));
    
    // legend
    const allN = clusterData.total_responses || chartData[0]?.allN || 0;
    const filteredN = chartData.reduce((sum, c) => Math.max(sum, c.filteredN), 0);
    
    const legendAll = {
        de: `Gesamt (n=${allN})`,
        fr: `Total (n=${allN})`,
        en: `Total (n=${allN})`,
        it: `Totale (n=${allN})`
    };
    const legendFiltered = {
        de: `${filterLabel} (n=${filteredN})`,
        fr: `${filterLabel} (n=${filteredN})`,
        en: `${filterLabel} (n=${filteredN})`,
        it: `${filterLabel} (n=${filteredN})`
    };
    
    // trace 1: overall data (grey)
    const traceAll = {
        name: pickLang(legendAll, lang),
        type: 'bar',
        orientation: 'h',
        y: labels,
        x: allPercentages,
        text: allPercentages.map(pct => `${pct.toFixed(1)}%`),
        textposition: 'outside',
        textfont: { size: 13, color: '#64748b' },
        marker: {
            color: '#cbd5e1',
            line: { color: '#94a3b8', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>' + t('total_label', lang) + ': %{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    // trace 2: filtered data (brand colour)
    const traceFiltered = {
        name: pickLang(legendFiltered, lang),
        type: 'bar',
        orientation: 'h',
        y: labels,
        x: filteredPercentages,
        text: chartData.map(c => {
            const pct = c.filteredPct.toFixed(1);
            // add the trend arrow
            if (Math.abs(c.diff) > 2) {
                const arrow = c.diff > 0 ? '↑' : '↓';
                return `${pct}% ${arrow}`;
            }
            return `${pct}%`;
        }),
        textposition: 'outside',
        textfont: { 
            size: 12, 
            color: chartData.map(c => {
                if (c.diff > 5) return '#059669';  // green when clearly higher
                if (c.diff < -5) return '#dc2626'; // red when clearly lower
                return '#0369a1';
            })
        },
        marker: {
            color: '#0ea5e9',
            line: { color: '#0284c7', width: 1 }
        },
        hovertemplate: '<b>%{y}</b><br>' + filterLabel + ': %{x:.1f}%<extra></extra>',
        cliponaxis: false
    };
    
    const barHeight = 55;
    const chartHeight = Math.max(250, chartData.length * barHeight + 100);
    const maxPct = Math.max(...allPercentages, ...filteredPercentages);
    
    const layout = {
        ...DEFAULT_LAYOUT,
        height: chartHeight,
        margin: { l: dynamicLeftMargin, r: 80, t: 40, b: 40 },
        barmode: 'group',
        bargap: 0.2,
        bargroupgap: 0.1,
        showlegend: true,
        legend: {
            orientation: 'h',
            y: 1.08,
            x: 0.5,
            xanchor: 'center',
            font: { size: 12 },
            bgcolor: 'rgba(255,255,255,0.8)',
            bordercolor: '#e2e8f0',
            borderwidth: 1
        },
        xaxis: {
            
            range: [0, Math.min(100, maxPct * 1.3)],
            ticksuffix: '%',
            gridcolor: '#e5e7eb',
            gridwidth: 1,
            zeroline: false
        },
        yaxis: {
            automargin: true,
            tickfont: { size: 13 }
        }
    };
    
    Plotly.newPlot(container, [traceAll, traceFiltered], layout, DEFAULT_CONFIG);
}

/**
 * Renders small multiples for segment comparisons
 * Shows the answer distribution broken down by the segment dimensions
 * 
 * @param {HTMLElement} container - DOM container
 * @param {Object} question - question object
 * @param {Object} segments - segment definitions
 * @param {string} lang - language code
 */
function renderSmallMultiples(container, question, segments, lang) {
    if (!question || !question.results) {
        container.innerHTML = '';
        return;
    }
    
    // unique id base from the id of the parent container
    const containerId = container.id || `sm-${Date.now()}`;
    const idPrefix = safeId(containerId.replace('sm-container-', '').replace('sm-container', ''));
    
    const results = question.results;
    // One accordion per configured dimension (data.segments), in configured order
    const segmentConfigs = Object.entries(segments || {}).map(([key, meta]) => {
        const label = meta.label || meta.labels || {};
        return { key, resultKey: `by_${key}`, icon: '📊',
                 label: typeof label === 'object' ? label : { [lang]: label } };
    });
    
    // is there segment data?
    const availableSegments = segmentConfigs.filter(s => 
        results[s.resultKey] && Object.keys(results[s.resultKey]).length > 0
    );
    
    if (availableSegments.length === 0) {
        container.innerHTML = '<div class="text-sm text-slate-400 text-center py-4">' + t('no_segmented_data', lang) + '</div>';
        return;
    }
    
    // determine the question types
    const isCooperationMatrix = question.type === 'cooperation_matrix';
    const isMatrixLikert = question.type === 'matrix_likert' || question.type === 'matrix';
    
    // accordions only for matrix_likert (it has many sub-items)
    const useAccordions = isMatrixLikert;
    
    // get the answer options of the question
    const options = getQuestionOptions(question, lang);
    
    if (useAccordions) {
        // with accordions for long question types (matrix_likert)
        container.innerHTML = `
            <div class="space-y-3">
                ${availableSegments.map((s, idx) => `
                    <div class="small-multiple-section border border-slate-200 rounded-lg overflow-hidden">
                        <button type="button" 
                                id="sma-btn-${idPrefix}-${safeId(s.key)}"
                                class="w-full px-4 py-3 bg-slate-50 hover:bg-slate-100 flex items-center justify-between cursor-pointer transition-colors"
                                onclick="toggleSmallMultipleAccordion('${idPrefix}', '${safeId(s.key)}')">
                            <div class="flex items-center gap-2 text-sm font-semibold text-slate-600">
                                <span>${s.icon}</span>
                                <span>${escapeHtml(s.label[lang] || Object.values(s.label).find(v => v) || s.key)}</span>
                            </div>
                            <svg id="sma-icon-${idPrefix}-${safeId(s.key)}" 
                                 class="w-5 h-5 text-slate-400 transition-transform" 
                                 fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
                            </svg>
                        </button>
                        <div id="sma-content-${idPrefix}-${safeId(s.key)}" 
                             class="px-4 py-3 hidden">
                            <div id="smc-${idPrefix}-${safeId(s.key)}" class="h-auto min-h-[150px]"></div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
        
        // keep the render functions for lazy loading
        const renderFunctions = {};
        availableSegments.forEach(segConfig => {
            renderFunctions[segConfig.key] = () => {
                const chartContainer = document.getElementById(`smc-${idPrefix}-${safeId(segConfig.key)}`);
                if (!chartContainer || chartContainer.dataset.rendered === 'true') return;
                
                const segmentData = results[segConfig.resultKey];
                renderMatrixLikertSmallMultiple(chartContainer, segmentData, question, segments, segConfig.key, lang);
                chartContainer.dataset.rendered = 'true';
            };
        });
        
        // keep the render functions globally for the toggle handler
        window._smRenderFunctions = window._smRenderFunctions || {};
        window._smRenderFunctions[idPrefix] = renderFunctions;
        
    } else {
        // without accordions for simple question types (single_choice, etc.)
        container.innerHTML = `
            <div class="space-y-6">
                ${availableSegments.map(s => `
                    <div class="small-multiple-section">
                        <div class="text-sm font-semibold text-slate-600 mb-3 flex items-center gap-2">
                            <span>${s.icon}</span>
                            <span>${escapeHtml(pickLang(s.label, lang))}</span>
                        </div>
                        <div id="smc-${idPrefix}-${safeId(s.key)}" class="h-auto min-h-[150px]"></div>
                    </div>
                `).join('')}
            </div>
        `;
        
        // render all segments at once
        setTimeout(() => {
            availableSegments.forEach(segConfig => {
                const chartContainer = document.getElementById(`smc-${idPrefix}-${safeId(segConfig.key)}`);
                if (!chartContainer) return;
                
                const segmentData = results[segConfig.resultKey];
                
                if (isCooperationMatrix) {
                    renderCooperationMatrixSmallMultiple(chartContainer, segmentData, segments, segConfig.key, lang);
                } else {
                    renderSegmentDistributionChart(chartContainer, segmentData, options, segments, segConfig.key, lang, question.colors);
                }
            });
        }, 50);
    }
}

/**
 * Toggle handler of the small multiple accordions
 */
function toggleSmallMultipleAccordion(idPrefix, segmentKey) {
    const content = document.getElementById(`sma-content-${idPrefix}-${segmentKey}`);
    const icon = document.getElementById(`sma-icon-${idPrefix}-${segmentKey}`);
    
    if (!content) return;
    
    const isHidden = content.classList.contains('hidden');
    
    if (isHidden) {
        // open
        content.classList.remove('hidden');
        icon?.classList.add('rotate-180');
        
        // lazy render if not rendered yet
        const renderFunctions = window._smRenderFunctions?.[idPrefix];
        if (renderFunctions?.[segmentKey]) {
            setTimeout(() => renderFunctions[segmentKey](), 50);
        }
    } else {
        // close
        content.classList.add('hidden');
        icon?.classList.remove('rotate-180');
    }
}

/**
 * Gets the answer options of a question
 */
function getQuestionOptions(question, lang) {
    if (!question) return [];
    
    // matrix_likert: use the scale
    if (question.type === 'matrix_likert' && question.scale) {
        if (Array.isArray(question.scale)) {
            return question.scale.map((s, i) => ({
                key: s.key || s,
                label: pickLang(s.label, lang) || s
            }));
        }
    }
    
    // other question types: use the options
    if (question.options) {
        // [{key, label: {de, fr, en}}]
        if (Array.isArray(question.options) && question.options[0]?.key) {
            return question.options.map(opt => ({
                key: opt.key,
                label: pickLang(opt.label, lang) || opt.key
            }));
        }
    }
    
    // fallback: keys from results.all.percentages
    if (question.results?.all?.percentages) {
        return Object.keys(question.results.all.percentages).map(key => ({
            key: key,
            label: key
        }));
    }
    
    return [];
}

/**
 * Renders a small multiple for a cooperation matrix
 * Shows the average importance over all partners per segment
 */
function renderCooperationMatrixSmallMultiple(container, segmentData, segments, segmentKey, lang) {
    if (!segmentData || Object.keys(segmentData).length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data', lang) + '</div>';
        return;
    }
    
    // importance levels with colours (grey -> blue gradient)
    // keys from the actual data
    const levelConfig = [
        { key: 'keine_zusammenarbeit', color: '#d1d5db', label: { de: 'Keine Zusammenarbeit', en: 'No cooperation', fr: 'Pas de coopération', it: 'Nessuna cooperazione' } },
        { key: 'von_geringer_bedeutung', color: '#93c5fd', label: { de: 'Geringe Bedeutung', en: 'Minor importance', fr: 'Faible importance', it: 'Importanza minore' } },
        { key: 'bedeutsam', color: '#3b82f6', label: { de: 'Bedeutsam', en: 'Significant', fr: 'Significatif', it: 'Significativo' } },
        { key: 'zentral_fuer_unsere_it_strategie', color: '#1e40af', label: { de: 'Zentral', en: 'Central', fr: 'Central', it: 'Centrale' } }
    ];
    
    // collect the data of every segment value
    const segmentKeys = [];
    const segmentLabels = [];
    const segmentNs = [];
    const levelData = {}; // levelKey -> [avg percentages per segment]
    
    // initialise levelData
    levelConfig.forEach(l => levelData[l.key] = []);
    
    for (const [segmentValue, data] of Object.entries(segmentData)) {
        if (!data || !data.n || data.n < surveyMinN()) continue;
        
        // is by_row present?
        const byRow = data.by_row;
        if (!byRow || Object.keys(byRow).length === 0) continue;
        
        // translate the label
        let label = segmentValue;
        if (segments?.[segmentKey]?.values) {
            const values = segments[segmentKey].values;
            if (Array.isArray(values) && values.length > 0 && values[0]?.key) {
                const found = values.find(v => v.key === segmentValue);
                if (found?.label) {
                    label = pickLang(found.label, lang) || segmentValue;
                }
            }
        }
        // fallback: use translateLabel
        if (label === segmentValue && typeof translateLabel === 'function') {
            label = translateLabel(segmentValue, lang);
        }
        
        if (label.length > 20) {
            label = label.substring(0, 18) + '...';
        }
        
        segmentKeys.push(segmentValue);
        segmentLabels.push(label);
        segmentNs.push(data.n);
        
        // aggregate the percentages over all partners for every level
        levelConfig.forEach(levelCfg => {
            let totalPct = 0;
            let partnerCount = 0;
            
            // iterate over all partners in by_row
            for (const [partner, partnerData] of Object.entries(byRow)) {
                const pct = partnerData?.percentages?.[levelCfg.key];
                if (typeof pct === 'number') {
                    totalPct += pct;
                    partnerCount++;
                }
            }
            
            // average over all partners
            const avgPct = partnerCount > 0 ? totalPct / partnerCount : 0;
            levelData[levelCfg.key].push(Math.round(avgPct * 10) / 10);
        });
    }
    
    if (segmentLabels.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data_min_n', lang).replace('{n}', surveyMinN()) + '</div>';
        return;
    }
    
    // sorting: configured order of the dimension, otherwise by n
    const segmentValues = segmentKeys.map((key, i) => ({ key, data: {} }));
    const sorted = sortSegmentData(segmentValues, segmentLabels, segmentNs, segmentKey, segments);
    const sortedLabels = sorted.sortedLabels;
    const sortedNs = sorted.sortedNs;
    const sortedIndices = sorted.sortedIndices;
    
    // create the traces
    const traces = levelConfig.map((levelCfg, idx) => {
        const sortedPcts = sortedIndices.map(i => levelData[levelCfg.key][i]);
        const levelLabel = pickLang(levelCfg.label, lang);
        
        return {
            type: 'bar',
            orientation: 'h',
            name: levelLabel,
            y: sortedLabels,
            x: sortedPcts,
            text: sortedPcts.map(p => p >= 8 ? `${Math.round(p)}%` : ''),
            textposition: 'inside',
            textfont: { size: 11, color: idx < 2 ? '#374151' : '#fff' },
            marker: { color: levelCfg.color },
            hovertemplate: sortedLabels.map((lbl, i) => 
                `<b>${lbl}</b><br>${levelLabel}: ${sortedPcts[i].toFixed(1)}%<br>n = ${sortedNs[i]}<extra></extra>`
            )
        };
    });
    
    const chartHeight = Math.max(150, sortedLabels.length * 40 + 60);
    
    const layout = {
        barmode: 'stack',
        margin: { l: 120, r: 20, t: 10, b: 40 },
        height: chartHeight,
        xaxis: {
            
            tickfont: { size: 13 },
            range: [0, 100],
            tickvals: [0, 25, 50, 75, 100],
            ticktext: ['0%', '25%', '50%', '75%', '100%'],
            gridcolor: '#e2e8f0',
            zeroline: false,
            fixedrange: true
        },
        yaxis: {
            tickfont: { size: 13 },
            automargin: true
        },
        legend: {
            orientation: 'h',
            yanchor: 'top',
            y: -0.2,
            xanchor: 'center',
            x: 0.5,
            font: { size: 12 }
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: true,
        bargap: 0.3
    };
    
    Plotly.newPlot(container, traces, layout, { responsive: true, displayModeBar: false });
}

/**
 * Renders a small multiple for matrix/Likert questions
 * Shows the average distribution of the scale values per segment
 */
function renderMatrixLikertSmallMultiple(container, segmentData, question, segments, segmentKey, lang) {
    if (!segmentData || Object.keys(segmentData).length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data', lang) + '</div>';
        return;
    }
    
    // trend keywords for the colours (fallback for scales without a configured polarity)
    const trendKeywords = {
        increase: ['zunahme', 'zugenommen', 'verstärkt', 'verstaerkt', 'erhöht', 'erhoeht', 'gestiegen', 'augmentation', 'increase', 'strengthened'],
        unchanged: ['unverändert', 'unveraendert', 'gleichgeblieben', 'keine veränderung', 'keine veraenderung', 'inchangé', 'unchanged', 'no change'],
        decrease: ['abnahme', 'abgenommen', 'verringert', 'vermindert', 'gesunken', 'diminution', 'decrease', 'reduced']
    };
    
    const getTrendType = (item) => {
        const polarity = configuredPolarity(question, item);
        if (polarity) return POLARITY_TREND[polarity] || null;
        const lower = (item || '').toLowerCase();
        if (trendKeywords.increase.some(k => lower.includes(k))) return 'increase';
        if (trendKeywords.decrease.some(k => lower.includes(k))) return 'decrease';
        if (trendKeywords.unchanged.some(k => lower.includes(k))) return 'unchanged';
        return null;
    };
    
    const trendColors = {
        increase: '#22c55e',  // green
        unchanged: '#9ca3af', // grey
        decrease: '#ef4444'   // red
    };
    
    // collect all segments with enough data
    const validSegments = [];
    for (const [segValue, data] of Object.entries(segmentData)) {
        if (data && data.n >= surveyMinN()) {
            // translate the label
            let label = segValue;
            if (segments?.[segmentKey]?.values) {
                const values = segments[segmentKey].values;
                if (Array.isArray(values) && values.length > 0 && values[0]?.key) {
                    const found = values.find(v => v.key === segValue);
                    if (found?.label) {
                        label = pickLang(found.label, lang) || segValue;
                    }
                }
            }
            validSegments.push({ key: segValue, label: label, data: data, n: data.n });
        }
    }
    
    if (validSegments.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data_min_n', lang).replace('{n}', surveyMinN()) + '</div>';
        return;
    }
    
    // sort the segments: configured order of the dimension, otherwise by n
    const order = configuredSegmentOrder(segments, segmentKey);
    if (order.length) {
        const pos = seg => { const p = order.indexOf(String(seg.key)); return p === -1 ? 999 : p; };
        validSegments.sort((a, b) => pos(a) - pos(b) || b.n - a.n);
    } else {
        validSegments.sort((a, b) => b.n - a.n);
    }
    
    // collect all rows (items) of the data
    const allRows = new Set();
    for (const seg of validSegments) {
        const itemsData = seg.data?.items || seg.data?.by_row;
        if (itemsData) {
            Object.keys(itemsData).forEach(k => allRows.add(k));
        }
    }
    
    // collect all scale values
    const allScaleKeys = new Set();
    for (const seg of validSegments) {
        const itemsData = seg.data?.items || seg.data?.by_row;
        if (itemsData) {
            for (const itemData of Object.values(itemsData)) {
                const dist = itemData?.distribution || itemData?.percentages;
                if (dist) {
                    Object.keys(dist).forEach(k => allScaleKeys.add(k));
                }
            }
        }
    }
    
    // sort the scale: increase -> unchanged -> decrease
    const scaleItems = Array.from(allScaleKeys).map(key => ({
        key,
        type: getTrendType(key),
        color: trendColors[getTrendType(key)] || COLORS.palette[0]
    }));
    
    const trendOrder = ['increase', 'unchanged', 'decrease'];
    scaleItems.sort((a, b) => {
        const aIdx = a.type ? trendOrder.indexOf(a.type) : 99;
        const bIdx = b.type ? trendOrder.indexOf(b.type) : 99;
        return aIdx - bIdx;
    });
    
    // filter only the relevant scale items (with a type)
    const relevantScaleItems = scaleItems.filter(s => s.type !== null);
    if (relevantScaleItems.length === 0) {
        // fallback: use all scale items
        relevantScaleItems.push(...scaleItems.slice(0, 3));
    }
    
    // get the row labels from question.items or question.rows
    const getRowLabel = (rowKey) => {
        // question.items may be an object or an array
        if (question.items) {
            if (Array.isArray(question.items)) {
                const item = question.items.find(i => i.key === rowKey || i.id === rowKey);
                if (item?.label) {
                    return pickLang(item.label, lang) || rowKey;
                }
            } else if (typeof question.items === 'object') {
                // items is an object with keys
                const item = question.items[rowKey];
                if (item?.label) {
                    return pickLang(item.label, lang) || rowKey;
                }
            }
        }
        // question.rows may also be an object or an array
        if (question.rows) {
            if (Array.isArray(question.rows)) {
                const row = question.rows.find(r => r.key === rowKey || r.id === rowKey);
                if (row?.label) {
                    return pickLang(row.label, lang) || rowKey;
                }
            } else if (typeof question.rows === 'object') {
                const row = question.rows[rowKey];
                if (row?.label) {
                    return pickLang(row.label, lang) || rowKey;
                }
            }
        }
        // fallback: format the key
        return rowKey.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    };
    
    // create the HTML with one chart per row
    const rowsArray = Array.from(allRows);
    
    // unique id base from container.id or a timestamp
    const idBase = container.id || `mlsm-${Date.now()}`;
    
    // create the container of all row charts
    container.innerHTML = `
        <div class="space-y-4">
            ${rowsArray.map((rowKey, idx) => `
                <div class="bg-white rounded border border-slate-200 p-3">
                    <div class="text-xs font-medium text-slate-700 mb-2">${escapeHtml(getRowLabel(rowKey))}</div>
                    <div id="${idBase}-row-${idx}" class="h-auto"></div>
                </div>
            `).join('')}
        </div>
    `;
    
    // render one chart per row
    setTimeout(() => {
        rowsArray.forEach((rowKey, idx) => {
            const chartContainer = document.getElementById(`${idBase}-row-${idx}`);
            if (!chartContainer) return;
            
            // collect the data of this row from all segments
            const segLabels = [];
            const segNs = [];
            const scaleData = {};
            relevantScaleItems.forEach(s => scaleData[s.key] = []);
            
            for (const seg of validSegments) {
                const itemsData = seg.data?.items || seg.data?.by_row;
                const rowData = itemsData?.[rowKey];
                
                if (rowData) {
                    const dist = rowData?.percentages || rowData?.distribution;
                    const rowN = rowData?.n || seg.n;
                    
                    if (rowN >= surveyMinN()) {
                        segLabels.push(`${seg.label} (n=${rowN})`);
                        segNs.push(rowN);
                        
                        relevantScaleItems.forEach(scaleItem => {
                            const pct = dist?.[scaleItem.key] || 0;
                            scaleData[scaleItem.key].push(Math.round(pct * 10) / 10);
                        });
                    }
                }
            }
            
            if (segLabels.length === 0) {
                chartContainer.innerHTML = '<div class="text-xs text-slate-400 text-center py-2">n < ' + surveyMinN() + '</div>';
                return;
            }
            
            // create the traces of the stacked bar chart
            const traces = relevantScaleItems.map(scaleItem => ({
                type: 'bar',
                orientation: 'h',
                name: scaleItem.key.charAt(0).toUpperCase() + scaleItem.key.slice(1),
                y: segLabels,
                x: scaleData[scaleItem.key],
                text: scaleData[scaleItem.key].map(p => p >= 8 ? `${Math.round(p)}%` : ''),
                textposition: 'inside',
                textfont: { size: 11, color: scaleItem.type === 'unchanged' ? '#374151' : '#fff' },
                marker: { color: scaleItem.color },
                hovertemplate: segLabels.map((lbl, i) => 
                    `<b>${lbl}</b><br>${scaleItem.key}: ${scaleData[scaleItem.key][i].toFixed(1)}%<extra></extra>`
                )
            }));
            
            const chartHeight = Math.max(60, segLabels.length * 28 + 20);
            
            const layout = {
                barmode: 'stack',
                margin: { l: 130, r: 10, t: 5, b: 5 },
                height: chartHeight,
                xaxis: {
                    tickfont: { size: 12 },
                    range: [0, 100],
                    tickvals: [0, 50, 100],
                    ticktext: ['0%', '50%', '100%'],
                    gridcolor: '#e2e8f0',
                    zeroline: false,
                    fixedrange: true
                },
                yaxis: {
                    tickfont: { size: 13 },
                    automargin: true
                },
                paper_bgcolor: 'rgba(0,0,0,0)',
                plot_bgcolor: 'rgba(0,0,0,0)',
                showlegend: false,
                bargap: 0.3
            };
            
            Plotly.newPlot(chartContainer, traces, layout, { responsive: true, displayModeBar: false });
        });
        
        // add the legend once at the end
        const legendHtml = `
            <div class="flex justify-center gap-4 mt-3 pt-2 border-t border-slate-100">
                ${relevantScaleItems.map(s => `
                    <span class="flex items-center gap-1 text-xs">
                        <span class="w-3 h-3 rounded" style="background: ${s.color}"></span>
                        ${escapeHtml(String(s.key).charAt(0).toUpperCase() + String(s.key).slice(1))}
                    </span>
                `).join('')}
            </div>
        `;
        container.insertAdjacentHTML('beforeend', legendHtml);
        
    }, 50);
}

/**
 * Renders a stacked bar chart of the segment distribution
 */
function renderSegmentDistributionChart(container, segmentData, options, segments, segmentKey, lang, customColors = null) {
    if (!segmentData || Object.keys(segmentData).length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data', lang) + '</div>';
        return;
    }
    
    // collect the data of every segment value
    const segmentValues = [];
    const segmentLabels = [];
    const segmentNs = [];
    
    for (const [segmentValue, data] of Object.entries(segmentData)) {
        if (!data || !data.n || data.n < surveyMinN()) continue;
        
        // translate the label
        let label = segmentValue;
        if (segments?.[segmentKey]?.values) {
            const values = segments[segmentKey].values;
            // [{key, label}]
            if (Array.isArray(values) && values.length > 0 && values[0]?.key) {
                const found = values.find(v => v.key === segmentValue);
                if (found?.label) {
                    label = pickLang(found.label, lang) || segmentValue;
                }
            }
        }
        
        // shorten long labels
        if (label.length > 25) {
            label = label.substring(0, 23) + '...';
        }
        
        segmentValues.push({ key: segmentValue, data: data });
        segmentLabels.push(label);
        segmentNs.push(data.n);
    }
    
    if (segmentValues.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-4">' + t('no_data_min_n', lang).replace('{n}', surveyMinN()) + '</div>';
        return;
    }
    
    // sorting: configured order of the dimension, otherwise by n
    const sorted = sortSegmentData(segmentValues, segmentLabels, segmentNs, segmentKey, segments);
    const sortedIndices = sorted.sortedIndices;
    const sortedLabels = sorted.sortedLabels;
    const sortedValues = sorted.sortedValues;
    const sortedNs = sorted.sortedNs;
    
    // collect ALL options that actually occur in the data
    const allOptionKeys = new Set();
    sortedValues.forEach(sv => {
        if (sv.data.percentages) {
            Object.keys(sv.data.percentages).forEach(k => allOptionKeys.add(k));
        }
    });
    
    // build the option list from the actual data
    const dataOptions = Array.from(allOptionKeys).map(key => {
        // try to find the label in question.options
        const optionDef = options.find(o => o.key === key || o.label === key);
        return {
            key: key,
            label: optionDef?.label || key
        };
    });
    
    // sort: budget (increased -> same -> decreased), yes/no, then the rest
    dataOptions.sort((a, b) => {
        const aLower = (a.label || a.key).toLowerCase();
        const bLower = (b.label || b.key).toLowerCase();
        
        // Budget-Keywords
        const budgetIncreased = ['erhöht', 'erhoeht', 'increased', 'gestiegen', 'augmenté'];
        const budgetUnchanged = ['gleichgeblieben', 'unchanged', 'stable', 'gleich', 'inchangé'];
        const budgetDecreased = ['vermindert', 'decreased', 'gesunken', 'diminué', 'reduziert'];
        
        const getBudgetOrder = (str) => {
            if (budgetIncreased.some(p => str.includes(p))) return 0; // increased first (left)
            if (budgetUnchanged.some(p => str.includes(p))) return 1; // then same
            if (budgetDecreased.some(p => str.includes(p))) return 2; // then decreased (right)
            return -1;
        };
        
        const aIsBudget = getBudgetOrder(aLower);
        const bIsBudget = getBudgetOrder(bLower);
        
        // both budget options: sort by the budget order
        if (aIsBudget >= 0 && bIsBudget >= 0) {
            return aIsBudget - bIsBudget;
        }
        // only one is a budget option
        if (aIsBudget >= 0 && bIsBudget < 0) return -1;
        if (aIsBudget < 0 && bIsBudget >= 0) return 1;
        
        // yes/no sorting
        const yesPatterns = ['ja', 'yes', 'oui'];
        const noPatterns = ['nein', 'no', 'non'];
        
        const aIsYes = yesPatterns.some(p => aLower === p || aLower.startsWith(p));
        const bIsYes = yesPatterns.some(p => bLower === p || bLower.startsWith(p));
        const aIsNo = noPatterns.some(p => aLower === p || aLower.startsWith(p));
        const bIsNo = noPatterns.some(p => bLower === p || bLower.startsWith(p));
        
        if (aIsYes && !bIsYes) return -1;
        if (!aIsYes && bIsYes) return 1;
        if (aIsNo && !bIsNo) return 1;
        if (!aIsNo && bIsNo) return -1;
        
        return aLower.localeCompare(bLower);
    });
    
    // create the traces for every answer option (stacked bars)
    const traces = [];
    
    // at most 8 options for readability
    const maxOptions = 8;
    const displayOptions = dataOptions.slice(0, maxOptions);
    const hasMore = dataOptions.length > maxOptions;
    
    // sums per segment for the normalisation to 100%
    const segmentSums = sortedValues.map(sv => {
        let sum = 0;
        if (sv.data.percentages) {
            Object.values(sv.data.percentages).forEach(v => sum += (v || 0));
        }
        return sum > 0 ? sum : 100;
    });
    
    displayOptions.forEach((option, optIdx) => {
        // normalise the percentages to 100%
        const percentages = sortedValues.map((sv, i) => {
            const rawPct = sv.data.percentages?.[option.key] || 0;
            const normalizedPct = (rawPct / segmentSums[i]) * 100;
            return Math.round(normalizedPct * 10) / 10;
        });
        
        // determine the colour: customColors > semantic colour > default
        let color;
        if (customColors && customColors[option.key]) {
            color = customColors[option.key];
        } else {
            color = getSemanticColor(option.key, option.label, optIdx);
        }
        
        traces.push({
            type: 'bar',
            orientation: 'h',
            name: option.label.length > 25 ? option.label.substring(0, 23) + '...' : option.label,
            y: sortedLabels,
            x: percentages,
            text: percentages.map(p => p >= 8 ? `${Math.round(p)}%` : ''),
            textposition: 'inside',
            textfont: { size: 11, color: '#fff' },
            marker: {
                color: color
            },
            hovertemplate: sortedLabels.map((lbl, i) => 
                `<b>${lbl}</b><br>${option.label}: ${percentages[i].toFixed(1)}%<br>n = ${sortedNs[i]}<extra></extra>`
            )
        });
    });
    
    // "Other" for the remaining options if needed
    if (hasMore) {
        const restOptions = dataOptions.slice(maxOptions);
        const restPercentages = sortedValues.map((sv, i) => {
            let sum = 0;
            restOptions.forEach(opt => {
                sum += sv.data.percentages?.[opt.key] || 0;
            });
            const normalizedPct = (sum / segmentSums[i]) * 100;
            return Math.round(normalizedPct * 10) / 10;
        });
        
        traces.push({
            type: 'bar',
            orientation: 'h',
            name: t('other_category', lang),
            y: sortedLabels,
            x: restPercentages,
            text: restPercentages.map(p => p >= 8 ? `${Math.round(p)}%` : ''),
            textposition: 'inside',
            textfont: { size: 11, color: '#fff' },
            marker: {
                color: '#94a3b8'
            },
            hovertemplate: sortedLabels.map((lbl, i) => 
                `<b>${lbl}</b><br>Sonstige: ${restPercentages[i].toFixed(1)}%<br>n = ${sortedNs[i]}<extra></extra>`
            )
        });
    }
    
    // height from the number of segments
    const chartHeight = Math.max(150, sortedLabels.length * 40 + 60);
    
    const layout = {
        barmode: 'stack',
        margin: { l: 120, r: 20, t: 10, b: 40 },
        height: chartHeight,
        xaxis: {
            
            tickfont: { size: 13 },
            range: [0, 100],
            tickvals: [0, 25, 50, 75, 100],
            ticktext: ['0%', '25%', '50%', '75%', '100%'],
            gridcolor: '#e2e8f0',
            zeroline: false,
            fixedrange: true
        },
        yaxis: {
            tickfont: { size: 13 },
            automargin: true
        },
        legend: {
            orientation: 'h',
            yanchor: 'top',
            y: -0.2,
            xanchor: 'center',
            x: 0.5,
            font: { size: 12 }
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: true,
        bargap: 0.3
    };
    
    const config = {
        responsive: true,
        displayModeBar: false
    };
    
    Plotly.newPlot(container, traces, layout, config);
}

// Window-Resize Handler
let resizeTimeout;
window.addEventListener('resize', () => {
    clearTimeout(resizeTimeout);
    resizeTimeout = setTimeout(() => {
        resizeAllCharts();
    }, 250);
});

/**
 * Renders a small bar chart of the segment distribution of a cluster
 * Shows how often the topic was mentioned in each segment
 */
function renderClusterSegmentChart(containerId, segValueData, segments, lang) {
    const container = document.getElementById(containerId);
    if (!container || !segValueData) return;
    
    // collect the segment data
    const segValues = [];
    const percentages = [];
    const counts = [];
    const ns = [];
    
    // sort by percentage (highest first for Plotly = ascending, as the last is on top)
    const sortedEntries = Object.entries(segValueData)
        .filter(([key, data]) => data && data.n >= surveyMinN())
        .sort((a, b) => a[1].percentage - b[1].percentage); // ascending: lowest at the bottom
    
    if (sortedEntries.length === 0) {
        container.innerHTML = '<div class="text-xs text-slate-400 text-center py-2">' + t('no_data_min_n', lang).replace('{n}', surveyMinN()) + '</div>';
        return;
    }
    
    sortedEntries.forEach(([segValueKey, data]) => {
        // translate the segment name
        let label = segValueKey;
        // label of the value in any dimension of the segment metadata
        for (const dim of Object.values(segments || {})) {
            const found = (dim?.values || []).find(v => v && v.key === segValueKey);
            if (found?.label) { label = pickLang(found.label, lang) || segValueKey; break; }
        }
        
        segValues.push(label);
        percentages.push(data.percentage || 0);
        counts.push(data.count || 0);
        ns.push(data.n || 0);
    });
    
    // colours by percentage (higher = darker)
    const maxPct = Math.max(...percentages);
    const colors = percentages.map(pct => {
        const intensity = 0.3 + (pct / maxPct) * 0.7;
        return `rgba(59, 130, 246, ${intensity})`; // blue with a variable intensity
    });
    
    const trace = {
        type: 'bar',
        orientation: 'h',
        y: segValues,
        x: percentages,
        text: percentages.map((p, i) => `${p.toFixed(0)}%`),
        textposition: 'outside',
        textfont: { size: 13, color: '#64748b' },
        marker: { color: colors },
        hovertemplate: segValues.map((c, i) => 
            `<b>${c}</b><br>${percentages[i].toFixed(1)}% (${counts[i]} ${t('of_total', lang)} ${ns[i]})<extra></extra>`
        )
    };
    
    const layout = {
        margin: { l: 80, r: 40, t: 5, b: 5 },
        height: Math.max(80, sortedEntries.length * 25),
        xaxis: {
            showticklabels: false,
            showgrid: false,
            zeroline: false,
            range: [0, Math.max(...percentages) * 1.3]
        },
        yaxis: {
            tickfont: { size: 13 },
            automargin: true
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        bargap: 0.3
    };
    
    Plotly.newPlot(container, [trace], layout, { 
        responsive: true, 
        displayModeBar: false,
        staticPlot: false
    });
}

/**
 * Renders a diverging bar chart of the segment deviations of free-text clusters
 * Shows only clusters with a deviation >= threshold
 * 
 * @param {string} containerId - id of the container element
 * @param {string} segValueKey - key of the segment value
 * @param {Array} deviations - array of cluster deviations
 * @param {number} threshold - deviation threshold in %
 * @param {string} lang - language code
 */
function renderSegmentDeviationChart(containerId, segValueKey, deviations, threshold, lang) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    // filter by the threshold
    const significantDeviations = deviations.filter(d => Math.abs(d.deviation) >= threshold);
    
    if (significantDeviations.length === 0) {
        container.innerHTML = '';
        return;
    }
    
    // sort: positive deviations first (descending), then negative ones (descending by magnitude)
    significantDeviations.sort((a, b) => {
        if (a.deviation >= 0 && b.deviation >= 0) return b.deviation - a.deviation;
        if (a.deviation < 0 && b.deviation < 0) return a.deviation - b.deviation;
        return b.deviation - a.deviation; // positive before negative
    });
    
    // for Plotly: reverse the order (the last element on top)
    significantDeviations.reverse();
    
    // prepare the data: do NOT shorten the labels, show the full length
    const labels = significantDeviations.map(d => d.clusterLabel);
    
    const values = significantDeviations.map(d => d.deviation);
    
    // colours: green for positive, red for negative deviations
    const colors = values.map(v => v >= 0 ? '#22c55e' : '#ef4444');
    
    // translations for the hover
    const totalLabel = t('total', lang);
    const vsLabel = 'vs.';
    
    // hover text with details (fully translated)
    const hoverText = significantDeviations.map(d => {
        const dirLabel = t(d.deviation >= 0 ? 'more_frequent' : 'less_frequent', lang).toLowerCase();
        
        return `<b>${d.clusterLabel}</b><br>` +
               `${d.segValuePercentage.toFixed(1)}% ${vsLabel} ${d.allPercentage.toFixed(1)}% ${totalLabel}<br>` +
               `<b>${d.deviation >= 0 ? '+' : ''}${d.deviation.toFixed(1)}%</b> (${dirLabel})<br>` +
               `n = ${d.segValueN}`;
    });
    
    // fixed generous margin for the labels (as in the other charts)
    const dynamicLeftMargin = 450;
    
    const trace = {
        type: 'bar',
        orientation: 'h',
        y: labels,
        x: values,
        text: values.map(v => (v >= 0 ? '+' : '') + v.toFixed(0) + ' ' + t('pp_unit', lang)),   // differences of shares: percentage points
        textposition: 'outside',
        textfont: { size: 13, color: '#374151' },
        cliponaxis: false,  // prevents cut-off texts
        marker: { 
            color: colors,
            line: { width: 0 }
        },
        hovertemplate: hoverText.map(t => t + '<extra></extra>')
    };
    
    // sensible range of the x axis with more buffer for the text labels
    const maxAbs = Math.max(...values.map(Math.abs));
    const xRange = Math.ceil(maxAbs / 10) * 10 + 20; // more buffer for the percentage labels
    
    const layout = {
        margin: { l: dynamicLeftMargin, r: 100, t: 20, b: 40 },  // more right margin
        height: Math.max(200, significantDeviations.length * 35 + 60),
        xaxis: {
            title: { 
                text: t('deviation_from_average', lang),
                font: { size: 12 }
            },
            tickfont: { size: 13 },
            range: [-xRange, xRange],
            zeroline: true,
            zerolinecolor: '#94a3b8',
            zerolinewidth: 2,
            gridcolor: '#e2e8f0',
            ticksuffix: ' ' + t('pp_unit', lang)
        },
        yaxis: {
            tickfont: { size: 13 },
            automargin: true
        },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        bargap: 0.25,
        // annotations for the legend
        annotations: [
            {
                x: xRange * 0.7,
                y: 1.05,
                xref: 'x',
                yref: 'paper',
                text: '● ' + t('more_frequent', lang),
                showarrow: false,
                font: { size: 10, color: '#22c55e' }
            },
            {
                x: -xRange * 0.7,
                y: 1.05,
                xref: 'x',
                yref: 'paper',
                text: '● ' + t('less_frequent', lang),
                showarrow: false,
                font: { size: 10, color: '#ef4444' }
            }
        ]
    };
    
    Plotly.newPlot(container, [trace], layout, { 
        responsive: true, 
        displayModeBar: false 
    });
    
    // resize after a short delay, to make sure the container has its full width
    setTimeout(() => {
        if (container && container.offsetWidth > 0) {
            Plotly.Plots.resize(container);
        }
    }, 100);
}

// export for ES6 modules (optional)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        COLORS,
        renderBarChart,
        renderComparisonChart,
        renderStackedBarChart,
        renderClusterChart,
        renderClusterComparisonChart,
        renderCooperationMatrixChart,
        renderSmallMultiples,
        renderClusterSegmentChart,
        renderSegmentDeviationChart,
        exportChart,
        resizeAllCharts
    };
}

// ============================================================
// NUMERIC QUESTION RENDERER (per specification)
// ============================================================

/**
 * Renders a numeric question with a statistics box and a histogram.
 * 
 * @param {HTMLElement} container - container element
 * @param {Object} question - question object with results
 * @param {Object} resultData - result data (may be question.results.all)
 * @param {string} lang - language (de/fr/en/it)
 */
function renderNumericChart(container, question, resultData, lang = 'de') {
    const result = resultData || question.results?.all || {};
    const statistics = result.statistics || {};
    const histogramBins = result.histogram_bins || [];
    const values = result.values || {};
    const unit = result.unit || '';
    const decimals = result.decimals ?? 2;
    
    // localised labels
    const labels = {
        de: {
            statistics: 'Statistik',
            n: 'Anzahl Antworten',
            n_invalid: 'Ungültige Werte',
            mean: 'Mittelwert',
            median: 'Median',
            std: 'Standardabweichung',
            min: 'Minimum',
            max: 'Maximum',
            q25: '25%-Quantil',
            q75: '75%-Quantil',
            outliers: 'Ausreißer',
            histogram: 'Verteilung'
        },
        fr: {
            statistics: 'Statistiques',
            n: 'Nombre de réponses',
            n_invalid: 'Valeurs invalides',
            mean: 'Moyenne',
            median: 'Médiane',
            std: 'Écart-type',
            min: 'Minimum',
            max: 'Maximum',
            q25: 'Quantile 25%',
            q75: 'Quantile 75%',
            outliers: 'Valeurs aberrantes',
            histogram: 'Distribution'
        },
        it: {
            statistics: 'Statistiche',
            n: 'Numero di risposte',
            n_invalid: 'Valori non validi',
            mean: 'Media',
            median: 'Mediana',
            std: 'Deviazione standard',
            min: 'Minimo',
            max: 'Massimo',
            q25: 'Quantile 25%',
            q75: 'Quantile 75%',
            outliers: 'Valori anomali',
            histogram: 'Distribuzione'
        },
        en: {
            statistics: 'Statistics',
            n: 'Number of responses',
            n_invalid: 'Invalid values',
            mean: 'Mean',
            median: 'Median',
            std: 'Standard deviation',
            min: 'Minimum',
            max: 'Maximum',
            q25: '25th percentile',
            q75: '75th percentile',
            outliers: 'Outliers',
            histogram: 'Distribution'
        }
    };
    const t = pickLang(labels, lang);
    
    // format function
    const fmt = (val) => {
        if (val === null || val === undefined) return '–';
        return Number(val).toFixed(decimals) + (unit ? ' ' + unit : '');
    };
    
    // empty the container
    container.innerHTML = '';
    
    // create the wrapper
    const wrapper = document.createElement('div');
    wrapper.className = 'numeric-chart-wrapper';
    wrapper.style.cssText = 'display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start;';
    
    // === STATISTICS BOX ===
    if (Object.keys(statistics).length > 0) {
        const statsBox = document.createElement('div');
        statsBox.className = 'numeric-stats-box';
        statsBox.style.cssText = 'flex: 0 0 280px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px;';
        
        let statsHtml = `<h4 style="margin: 0 0 12px 0; font-size: 14px; font-weight: 600; color: #334155;">${t.statistics}</h4>`;
        statsHtml += '<table style="width: 100%; font-size: 13px; border-collapse: collapse;">';
        
        const rows = [
            ['n', statistics.n],
            ['mean', statistics.mean],
            ['median', statistics.median],
            ['std', statistics.std],
            ['min', statistics.min],
            ['max', statistics.max],
            ['q25', statistics.q25],
            ['q75', statistics.q75]
        ];
        
        rows.forEach(([key, val]) => {
            if (val !== undefined && val !== null) {
                const displayVal = (key === 'n' || key === 'n_invalid') ? val : fmt(val);
                statsHtml += `
                    <tr style="border-bottom: 1px solid #e2e8f0;">
                        <td style="padding: 6px 0; color: #64748b;">${t[key]}</td>
                        <td style="padding: 6px 0; text-align: right; font-weight: 500; color: #1e293b;">${displayVal}</td>
                    </tr>`;
            }
        });
        
        // invalid values
        if (statistics.n_invalid && statistics.n_invalid > 0) {
            statsHtml += `
                <tr style="border-bottom: 1px solid #e2e8f0; background: #fef3c7;">
                    <td style="padding: 6px 0; color: #92400e;">${t.n_invalid}</td>
                    <td style="padding: 6px 0; text-align: right; font-weight: 500; color: #92400e;">${statistics.n_invalid}</td>
                </tr>`;
        }
        
        statsHtml += '</table>';
        
        // outliers
        if (statistics.outliers && statistics.outliers.length > 0) {
            statsHtml += `
                <div style="margin-top: 12px; padding: 8px; background: #fef9c3; border-radius: 4px; font-size: 12px;">
                    <span style="color: #854d0e;">⚠️ ${t.outliers} (${statistics.outliers.length}): </span>
                    <span style="color: #713f12;">${statistics.outliers.map(v => fmt(v)).join(', ')}</span>
                </div>`;
        }
        
        statsBox.innerHTML = statsHtml;
        wrapper.appendChild(statsBox);
    }
    
    // === HISTOGRAM ===
    const chartContainer = document.createElement('div');
    chartContainer.className = 'numeric-histogram';
    chartContainer.style.cssText = 'flex: 1; min-width: 300px;';
    
    // prepare the data for the chart
    let chartData = [];
    if (histogramBins.length > 0) {
        chartData = histogramBins.map(bin => ({
            label: bin.label,
            value: bin.count
        }));
    } else if (Object.keys(values).length > 0) {
        chartData = Object.entries(values).map(([label, count]) => ({
            label,
            value: count
        }));
    }
    
    let drawChart = null;
    if (chartData.length > 0) {
        const chartDiv = document.createElement('div');
        chartDiv.id = 'numeric-chart-' + Math.random().toString(36).substr(2, 9);
        chartDiv.style.cssText = 'width: 100%; height: 300px;';
        chartContainer.appendChild(chartDiv);
        
        // Plotly histogram
        const trace = {
            x: chartData.map(d => d.label),
            y: chartData.map(d => d.value),
            type: 'bar',
            marker: {
                color: '#3b82f6',
                line: { color: '#1e40af', width: 1 }
            },
            hovertemplate: '%{x}<br>n = %{y}<extra></extra>'
        };
        
        const layout = {
            margin: { t: 30, r: 20, b: 60, l: 50 },
            xaxis: {
                title: { text: unit, font: { size: 12 } },
                tickangle: -45
            },
            yaxis: {
                title: { text: 'n', font: { size: 12 } }
            },
            bargap: 0.1,
            plot_bgcolor: 'rgba(0,0,0,0)',
            paper_bgcolor: 'rgba(0,0,0,0)'
        };
        
        // Plot once the element is in the document: Plotly looks the id up
        // there (a detached element is not found and the chart never appears)
        drawChart = () => Plotly.newPlot(chartDiv, [trace], layout, { responsive: true, displayModeBar: false });
    } else {
        chartContainer.innerHTML = '<p style="color: #64748b; font-size: 13px;">' + t('no_data_available', lang) + '</p>';
    }
    
    wrapper.appendChild(chartContainer);
    container.appendChild(wrapper);
    if (drawChart) drawChart();
}

// export for global access
window.renderNumericChart = renderNumericChart;

/**
 * Several numbers per respondent (handler numeric_matrix): mean per field as
 * horizontal bars, a table with n, mean, median, SD, min, max (and the mean
 * share of the total for constant sums) and, for constant sums, the share of
 * respondents whose fields add up to the total. Works on the filtered result
 * like every other renderer (resultData = results of the active segment).
 */
function renderNumericMatrixChart(container, question, resultData, lang = 'de') {
    container.innerHTML = '';
    const fields = resultData?.fields || {};
    const items = (question.items || []).filter(it => fields[it.key]);
    if (!items.length) {
        container.innerHTML = `<p class="text-sm text-slate-500">${escapeHtml(t('insufficient_data', lang))}</p>`;
        return;
    }
    const decimals = question.decimals ?? 1;
    const unit = question.unit ? ` ${question.unit}` : '';
    const fmt = v => (v === null || v === undefined || isNaN(v)) ? '–'
        : Number(v).toLocaleString(lang === 'en' ? 'en-US' : `${lang}-${lang.toUpperCase()}`,
                                   { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    const labels = items.map(it => pickLang(it.label, lang) || it.key);
    const means = items.map(it => fields[it.key].mean);

    const chartDiv = document.createElement('div');
    chartDiv.id = 'numeric-matrix-' + Math.random().toString(36).substr(2, 9);
    chartDiv.style.height = Math.max(160, items.length * 34 + 60) + 'px';
    container.appendChild(chartDiv);
    if (typeof Plotly !== 'undefined' && Plotly.newPlot) {
        Plotly.newPlot(chartDiv.id, [{
            type: 'bar', orientation: 'h', x: means.slice().reverse(), y: labels.slice().reverse(),
            text: means.slice().reverse().map(v => fmt(v) + unit), textposition: 'auto',
            marker: { color: '#3b82f6' },
            hovertemplate: '%{y}: %{x}<extra></extra>'
        }], {
            margin: { l: 10, r: 10, t: 10, b: 40 }, xaxis: { title: t('nm_mean', lang) + unit, zeroline: true },
            yaxis: { automargin: true }, paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
        }, { responsive: true, displayModeBar: false });
    }

    const cs = resultData.constant_sum;
    const heads = [t('nm_field', lang), 'n', t('nm_mean', lang), t('nm_median', lang), t('nm_sd', lang),
                   t('nm_min', lang), t('nm_max', lang)].concat(cs ? [t('nm_share_of_total', lang)] : []);
    const rows = items.map((it, i) => {
        const f = fields[it.key];
        const cells = [labels[i], f.n, fmt(f.mean), fmt(f.median), fmt(f.std), fmt(f.min), fmt(f.max)]
            .concat(cs ? [fmt(cs.mean_share?.[it.key]) + ' %'] : []);
        return '<tr>' + cells.map((c, j) => `<td class="px-2 py-1 ${j ? 'text-right' : ''}">${escapeHtml(String(c))}</td>`).join('') + '</tr>';
    }).join('');
    const table = document.createElement('div');
    table.className = 'overflow-x-auto mt-2';
    table.innerHTML = `<table class="text-sm w-full"><thead><tr>${heads.map((h, j) =>
        `<th class="px-2 py-1 font-semibold text-slate-600 ${j ? 'text-right' : 'text-left'}">${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
    container.appendChild(table);
    if (cs) {
        const note = document.createElement('p');
        note.className = 'text-xs text-slate-500 mt-2';
        note.textContent = t('nm_sum_note', lang).replace('{share}', fmt(cs.share_matching))
            .replace('{n}', cs.n_respondents).replace('{total}', fmt(cs.total));
        container.appendChild(note);
    }
}

/**
 * Dates (handler date): counts per period as vertical bars (days, months or
 * years, in time order, empty periods included) and a line with range and
 * median. Period labels per language come from the export (period_labels).
 */
function renderDateChart(container, question, resultData, lang = 'de') {
    container.innerHTML = '';
    const values = resultData?.values || {};
    const keys = Object.keys(values);
    if (!keys.length) {
        container.innerHTML = `<p class="text-sm text-slate-500">${escapeHtml(t('insufficient_data', lang))}</p>`;
        return;
    }
    const labels = keys.map(k => pickLang(question.period_labels?.[k], lang) || k);
    const chartDiv = document.createElement('div');
    chartDiv.id = 'date-chart-' + Math.random().toString(36).substr(2, 9);
    chartDiv.style.height = '320px';
    container.appendChild(chartDiv);
    if (typeof Plotly !== 'undefined' && Plotly.newPlot) {
        Plotly.newPlot(chartDiv.id, [{
            type: 'bar', x: labels, y: keys.map(k => values[k]), marker: { color: '#3b82f6' },
            text: keys.map(k => String(values[k])), textposition: 'auto',
            hovertemplate: '%{x}: %{y}<extra></extra>'
        }], {
            margin: { l: 40, r: 10, t: 10, b: 80 }, yaxis: { title: t('date_count', lang), rangemode: 'tozero' },
            xaxis: { type: 'category', tickangle: labels.length > 8 ? -45 : 0 },
            paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)'
        }, { responsive: true, displayModeBar: false });
    }
    const d = resultData.date || {};
    const fmt = iso => {
        if (!iso) return '–';
        const [y, m, dd] = iso.split('-');
        return lang === 'en' ? iso : (lang === 'de' ? `${dd}.${m}.${y}` : `${dd}/${m}/${y}`);
    };
    const note = document.createElement('p');
    note.className = 'text-xs text-slate-500 mt-2';
    note.textContent = t('date_range_note', lang).replace('{first}', fmt(d.first)).replace('{last}', fmt(d.last))
        .replace('{median}', fmt(d.median)).replace('{n}', resultData.n ?? '');
    container.appendChild(note);
}

/**
 * Scale metrics below a chart: mean, median, top/bottom box, n on the
 * scale and answers outside it. ``rows``: [{label, metrics}] (one entry for a
 * single choice, one per row for a matrix).
 */
function renderScaleMetrics(container, rows, lang = 'de', labelOf = l => l) {
    rows = (rows || []).filter(r => r.metrics);
    if (!rows.length) return;
    const fmt = (v, d = 1) => (v === null || v === undefined) ? '–'
        : Number(v).toLocaleString(lang === 'en' ? 'en-US' : `${lang}-${lang.toUpperCase()}`,
                                   { minimumFractionDigits: d, maximumFractionDigits: d });
    const box = rows[0].metrics.box || 2;
    const heads = (rows.length > 1 ? [t('nm_field', lang)] : []).concat([t('scale_mean', lang), t('nm_median', lang),
        t('scale_top', lang).replace('{k}', box), t('scale_bottom', lang).replace('{k}', box), 'n']);
    const body = rows.map(r => {
        const m = r.metrics;
        const cells = (rows.length > 1 ? [r.label] : []).concat([fmt(m.mean, 2), fmt(m.median, 1),
            fmt(m.top_box) + ' %', fmt(m.bottom_box) + ' %', m.n + (m.n_excluded ? ` (+${m.n_excluded})` : '')]);
        return '<tr>' + cells.map((c, j) => `<td class="px-2 py-1 ${j || rows.length === 1 ? 'text-right' : ''}">${escapeHtml(String(c))}</td>`).join('') + '</tr>';
    }).join('');
    const box_ = document.createElement('div');
    box_.className = 'overflow-x-auto mt-2 scale-metrics';
    box_.innerHTML = `<table class="text-xs w-full"><thead><tr>${heads.map((h, j) =>
        `<th class="px-2 py-1 font-semibold text-slate-600 ${j || rows.length === 1 ? 'text-right' : 'text-left'}">${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>
        <p class="text-xs text-slate-500 mt-1">${escapeHtml(t('scale_note', lang).replace('{min}', fmt(rows[0].metrics.min_value, 0)).replace('{max}', fmt(rows[0].metrics.max_value, 0)))}
        ${escapeHtml(t('scale_boxes', lang).replace('{top}', (rows[0].metrics.top_labels || []).map(l => labelOf(l)).join(', '))
            .replace('{bottom}', (rows[0].metrics.bottom_labels || []).map(l => labelOf(l)).join(', ')))}</p>`;
    container.appendChild(box_);
}

/** Net Promoter Score below the chart of an NPS question. */
function renderNpsNote(container, nps, lang = 'de') {
    if (!nps) return;
    const fmt = v => Math.round(v);
    const p = document.createElement('p');
    p.className = 'text-sm font-semibold text-slate-700 mt-2 nps-note';
    p.textContent = t('nps_note', lang).replace('{nps}', (nps.nps > 0 ? '+' : '') + fmt(nps.nps))
        .replace('{promoters}', fmt(nps.promoters)).replace('{passives}', fmt(nps.passives))
        .replace('{detractors}', fmt(nps.detractors)).replace('{n}', nps.n);
    container.appendChild(p);
}
