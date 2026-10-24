// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 Malte Dreyer
/**
 * i18n.js - internationalisation of the survey dashboard
 * Supported languages: German (de), French (fr), English (en), Italian (it)
 */

const i18n = {
    de: {
        no_about_configured: 'Für diese Umfrage ist keine Beschreibung hinterlegt (settings.survey_metadata.about).',
        nps_note: 'NPS {nps} (Promotoren {promoters} %, Passive {passives} %, Detraktoren {detractors} %; n = {n})',
        scale_boxes: 'Oberste Punkte: {top}; unterste Punkte: {bottom}.',
        scale_mean: 'Mittelwert',
        scale_top: 'Top-{k}',
        scale_bottom: 'Bottom-{k}',
        scale_note: 'Skala von {min} bis {max}; Antworten außerhalb der Skala (z. B. „weiß nicht“) in Klammern, nicht mitgerechnet.',
        date_count: 'Anzahl',
        date_range_note: 'n = {n}; von {first} bis {last}; Median {median}',
        nm_field: 'Feld',
        nm_mean: 'Mittelwert',
        nm_median: 'Median',
        nm_sd: 'SD',
        nm_min: 'Min',
        nm_max: 'Max',
        nm_share_of_total: 'Anteil an der Summe',
        nm_sum_note: '{share} % der Befragten (n = {n}) kommen auf die Summe {total}.',
        compare_by: 'Vergleich nach',
        show_specific_clusters: 'Freitext: segmenteigene Cluster statt Gesamt-Cluster zeigen',
        // Header
        title: 'Umfrage',  // replaced by metadata.survey_title when the export has one
        subtitle: 'Interaktives Ergebnis-Dashboard',
        
        // Filter
        filter: 'Filter',
        no_filter: 'Kein Filter',
        all: 'Alle',
        
        // sample
        sample_size: 'Stichprobe',
        total: 'gesamt',
        filtered: 'Gefiltert',
        insufficient_data: 'Nicht genügend Daten für eine Auswertung',
        insufficient_data_short: 'Zu wenig Daten für diese Kombination',
        basis: 'Basis',
        
        // themes
        no_questions: 'Keine Fragen in diesem Themenbereich',
        
        // Cluster-Details
        cluster_details: 'Cluster-Details anzeigen',
        segment_comparison: 'Vergleich nach Segmenten',
        jump_to: 'Springe zu:',
        examples: 'Beispiele',
        
        // free-text questions: mentions/persons
        mentions: 'Nennungen',
        persons: 'Personen',
        from: 'von',
        
        // Navigation Modes
        view_all_questions: 'Alle Fragen',
        more_questions: 'Weitere Fragen',
        view_by_themes: 'Nach Themen',
        view_my_profile: 'Eigene Auswahl',
        
        // home / overview
        survey_title: '',  // loaded from metadata.survey_title
        survey_subtitle: '',  // loaded from metadata
        download_publication: 'Publikation herunterladen',
        start_exploration: 'Zur Auswertung',
        participants: 'Teilnehmende',
        segValues: 'Gruppen',
        survey_period: 'Erhebung',
        about_survey: 'Über die Umfrage',
        how_to_use: 'So nutzen Sie das Dashboard',
        nav_help_questions: 'Chronologische Übersicht aller Fragen',
        nav_help_themes: 'Fragen gruppiert nach Themenbereichen',
        nav_help_correlations: 'Korrelationen zwischen Fragen als Heatmap oder Netzwerk',
        nav_help_segments: 'Unterschiede zwischen Gruppen',
        nav_help_profile: 'Nach den Gruppen der Umfrage filtern',
        
        // summary
        summary_title: 'Zusammenfassung der Ergebnisse',
        summary_subtitle: 'Die wichtigsten Ergebnisse der Umfrage',
        
        // Keywords
        
        // own selection (profile view)
        my_profile_settings: 'Mein Profil',
        profile_active: 'Profil-Filter aktiv',
        of_total: 'von',
        responses: 'Antworten',
        freetext_clustered_separately: 'Cluster separat erstellt',
        freetext_cluster_hint: 'Die Cluster-Kategorien wurden separat für dieses Segment erstellt und sind nicht direkt mit den Gesamtergebnissen vergleichbar.',
        apply_filter: 'Filter anwenden',
        reset_filter: 'Zurücksetzen',
        filter_fallback_hint: 'Für diese Filterkombination sind nicht genügend Daten vorhanden. Es werden Ergebnisse für eine Teilauswahl angezeigt.',
        
        // groups
        
        // Chart-Export
        export: 'Export',
        export_chart: 'Chart exportieren',
        
        // general
        loading: 'Wird geladen...',
        error_title: 'Fehler beim Laden',
        retry: 'Erneut versuchen',
        no_segmented_results: 'Segmentierte Ergebnisse nicht verfügbar',
        no_segmented_results_hint: 'Die Gesamtergebnisse werden angezeigt. Für segmentierte Auswertungen müssen die Daten mit aufgeschlüsselten Ergebnissen exportiert werden.',
        no_segments_defined: 'Für diese Umfrage sind keine Segmentierungskriterien definiert.',
        profile_info_multiselect: 'Wählen Sie eine oder mehrere Optionen pro Kategorie. Mehrfachauswahl ist möglich.',
        select_all: 'Alle auswählen',
        deselect_all: 'Alle abwählen',
        
        // Footer
        methodology: 'Methodik',
        imprint: 'Impressum',
        contact: 'Kontakt',
        privacy: 'Datenschutz',
        
        // Segment-Labels
        
        // scale labels (for Likert)
        
        // correlations
        view_correlations: 'Zusammenhänge',
        corr_view_cards: 'Übersicht',
        corr_view_heatmap: 'Heatmap',
        corr_view_network: 'Netzwerk',
        corr_total: 'Korrelationen gesamt',
        corr_strong: 'Stark',
        corr_medium: 'Mittel',
        corr_weak: 'Schwach',
        possible_reading: 'Mögliche Lesart',
        group_differences_tab: 'Getestete Unterschiede',
        group_differences_intro: 'Statistisch abgesicherte Unterschiede zwischen den Gruppen: getestet je Antwortoption, Item und Thema, p-Werte je Frage für die Zahl der Tests korrigiert (Benjamini-Hochberg). Aufgeführt ab schwacher Effektstärke und, bei Anteilen, ab {points} Prozentpunkten Abstand. Ein Unterschied ist keine Ursache; die „mögliche Lesart“ ist eine Deutung, kein Befund.',
        no_group_differences_data: 'Keine getesteten Gruppenunterschiede vorhanden (Pipeline mit Segmentvergleich ausführen).',
        group_differences_without: 'Ohne aufgeführten Unterschied',
        group_differences_summary: 'Zusammenfassung',
        adjusted_p: 'q (korrigiert)',
        measure_cramers_v: "Cramér's V",
        measure_kendall_tau_b: 'Kendalls τ-b',
        measure_epsilon_squared: 'ε²',
        filter_by_strength: 'Filtern nach Stärke:',
        correlations: 'Korrelationen',
        cross_theme_correlations: 'Themenübergreifende Zusammenhänge',
        correlation_matrix: 'Korrelationsmatrix',
        correlation_network: 'Korrelationsnetzwerk',
        questions: 'Fragen',
        no_correlations: 'Keine Korrelationsdaten verfügbar',
        
        // segment comparison
        view_segment_comparison: 'Gruppenvergleich',
        segment_clusters_tab: 'Segmentspezifische Themen',
        segment_deviations_tab: 'Abweichungen vom Durchschnitt',
        no_segment_clusters_data: 'Keine segmentspezifischen Cluster verfügbar (in der Konfiguration enable_specific_clusters setzen).',
        deviation_threshold: 'Schwellenwert Abweichung:',
        no_segment_cluster_data: 'Keine Freitext-Cluster mit Gruppendaten verfügbar.',
        no_significant_deviations: 'Keine Abweichungen über diesem Schwellenwert (Prozentpunkte, kein statistischer Test).',
        respondents: 'Teilnehmende',
        more_frequent: 'Häufiger',
        less_frequent: 'Seltener',
        no_correlations_for_filter: 'Keine Korrelationen für diesen Stärke-Filter gefunden',
        no_summary_configured: 'Für diese Umfrage ist keine Zusammenfassung hinterlegt (settings.survey_metadata.summary).',
        // texts of the views and charts
        no_data: 'Keine Daten',
        no_data_available: 'Keine Daten verfügbar',
        no_data_min_n: 'Keine Daten (n < {n})',
        no_segmented_data: 'Keine segmentierten Daten verfügbar',
        calc_note: 'Hinweis zur Berechnung:',
        other_category: 'Sonstige',
        deviation_from_average: 'Abweichung vom Durchschnitt (Prozentpunkte)',
        pp_unit: 'Pp.',
        low_n_short: 'Geringe Fallzahl',
        too_few_data: 'Zu wenige Daten',
        partial_selection: 'Teilauswahl',
        total_label: 'Gesamt',
        selection: 'Auswahl',
    },
    
    fr: {
        no_about_configured: 'Aucune description n\'est configurée pour cette enquête (settings.survey_metadata.about).',
        nps_note: 'NPS {nps} (promoteurs {promoters} %, passifs {passives} %, détracteurs {detractors} % ; n = {n})',
        scale_boxes: 'Points supérieurs : {top} ; points inférieurs : {bottom}.',
        scale_mean: 'Moyenne',
        scale_top: 'Top {k}',
        scale_bottom: 'Bottom {k}',
        scale_note: 'Échelle de {min} à {max} ; réponses hors échelle (p. ex. « je ne sais pas ») entre parenthèses, non comptées.',
        date_count: 'Nombre',
        date_range_note: 'n = {n} ; du {first} au {last} ; médiane {median}',
        nm_field: 'Champ',
        nm_mean: 'Moyenne',
        nm_median: 'Médiane',
        nm_sd: 'É.-T.',
        nm_min: 'Min',
        nm_max: 'Max',
        nm_share_of_total: 'Part du total',
        nm_sum_note: '{share} % des répondants (n = {n}) atteignent le total {total}.',
        compare_by: 'Comparer par',
        show_specific_clusters: 'Texte libre : afficher les clusters propres au segment',
        // Header
        title: 'Enquête',
        subtitle: 'Tableau de bord interactif',
        
        // Filter
        filter: 'Filtre',
        no_filter: 'Pas de filtre',
        all: 'Tous',
        
        // sample
        sample_size: 'Échantillon',
        total: 'au total',
        filtered: 'Filtré',
        insufficient_data: 'Données insuffisantes pour l\'analyse',
        insufficient_data_short: 'Trop peu de données pour cette combinaison',
        basis: 'Base',
        
        // themes
        no_questions: 'Aucune question dans ce domaine',
        
        // Cluster-Details
        cluster_details: 'Afficher les détails des clusters',
        segment_comparison: 'Comparaison par segments',
        jump_to: 'Aller à:',
        examples: 'Exemples',
        
        // free-text questions: mentions/persons
        mentions: 'mentions',
        persons: 'personnes',
        from: 'de',
        
        // Navigation Modes
        view_all_questions: 'Toutes les questions',
        more_questions: 'Autres questions',
        view_by_themes: 'Par thèmes',
        view_my_profile: 'Ma sélection',
        
        // Home / Accueil
        survey_title: '',
        survey_subtitle: '',
        download_publication: 'Télécharger la publication',
        start_exploration: 'Vers l\'analyse',
        participants: 'Participants',
        segValues: 'Groupes',
        survey_period: 'Enquête',
        about_survey: 'À propos de l\'enquête',
        how_to_use: 'Comment utiliser ce tableau de bord',
        nav_help_questions: 'Aperçu chronologique de toutes les questions',
        nav_help_themes: 'Questions regroupées par thèmes',
        nav_help_correlations: 'Corrélations entre questions sous forme de carte thermique ou réseau',
        nav_help_segments: 'Différences entre les groupes',
        nav_help_profile: 'Filtrer selon les groupes de l\'enquête',
        
        // Summary / Résumé
        summary_title: 'Résumé des résultats',
        summary_subtitle: 'Les principaux résultats de l\'enquête',
        
        // Keywords
        
        // own selection (profile view)
        my_profile_settings: 'Mon profil',
        profile_active: 'Filtre de profil actif',
        of_total: 'sur',
        responses: 'réponses',
        freetext_clustered_separately: 'Clusters créés séparément',
        freetext_cluster_hint: 'Les catégories de clusters ont été créées séparément pour ce segment et ne sont pas directement comparables aux résultats globaux.',
        apply_filter: 'Appliquer le filtre',
        reset_filter: 'Réinitialiser',
        filter_fallback_hint: 'Les données sont insuffisantes pour cette combinaison de filtres. Les résultats sont affichés pour une sélection partielle.',
        
        // groups
        
        // Chart-Export
        export: 'Exporter',
        export_chart: 'Exporter le graphique',
        
        // general
        loading: 'Chargement...',
        error_title: 'Erreur de chargement',
        retry: 'Réessayer',
        no_segmented_results: 'Résultats segmentés non disponibles',
        no_segmented_results_hint: 'Les résultats globaux sont affichés.',
        no_segments_defined: 'Aucun critère de segmentation n\'est défini pour cette enquête.',
        profile_info_multiselect: 'Sélectionnez une ou plusieurs options par catégorie. La sélection multiple est possible.',
        select_all: 'Tout sélectionner',
        deselect_all: 'Tout désélectionner',
        
        // Footer
        methodology: 'Méthodologie',
        imprint: 'Mentions légales',
        contact: 'Contact',
        privacy: 'Confidentialité',
        
        // Segment-Labels
        
        // scale labels
        
        // correlations
        view_correlations: 'Corrélations',
        corr_view_cards: 'Aperçu',
        corr_view_heatmap: 'Carte thermique',
        corr_view_network: 'Réseau',
        corr_total: 'Corrélations totales',
        corr_strong: 'Forte',
        corr_medium: 'Moyenne',
        corr_weak: 'Faible',
        possible_reading: 'Lecture possible',
        group_differences_tab: 'Différences testées',
        group_differences_intro: 'Différences statistiquement étayées entre les groupes : testées par option de réponse, item et thème, valeurs p corrigées par question pour le nombre de tests (Benjamini-Hochberg). Retenues à partir d\'un effet faible et, pour les parts, d\'un écart de {points} points de pourcentage. Une différence n\'est pas une cause ; la « lecture possible » est une interprétation, pas un résultat.',
        no_group_differences_data: 'Aucune différence testée entre les groupes disponible (exécuter le pipeline avec la comparaison des segments).',
        group_differences_without: 'Sans différence retenue',
        group_differences_summary: 'Synthèse',
        adjusted_p: 'q (corrigé)',
        measure_cramers_v: 'V de Cramér',
        measure_kendall_tau_b: 'τ-b de Kendall',
        measure_epsilon_squared: 'ε²',
        filter_by_strength: 'Filtrer par force:',
        correlations: 'Corrélations',
        cross_theme_correlations: 'Corrélations inter-thématiques',
        correlation_matrix: 'Matrice de corrélation',
        correlation_network: 'Réseau de corrélations',
        questions: 'Questions',
        no_correlations: 'Aucune donnée de corrélation disponible',
        no_correlations_for_filter: 'Aucune corrélation trouvée pour ce filtre de force',
        
        // Comparaison par pays
        view_segment_comparison: 'Comparaison des groupes',
        segment_clusters_tab: 'Thèmes propres aux segments',
        segment_deviations_tab: 'Écarts par rapport à la moyenne',
        no_segment_clusters_data: 'Aucun cluster propre aux segments disponible (activer enable_specific_clusters dans la configuration).',
        deviation_threshold: 'Seuil d\'écart:',
        no_segment_cluster_data: 'Aucun cluster de texte libre avec des données de groupe disponible.',
        no_significant_deviations: 'Aucun écart au-dessus de ce seuil (points de pourcentage, pas de test statistique).',
        respondents: 'Répondants',
        more_frequent: 'Plus fréquent',
        less_frequent: 'Moins fréquent',
        no_summary_configured: 'Aucun résumé n\'est configuré pour cette enquête (settings.survey_metadata.summary).',
        // texts of the views and charts
        no_data: 'Pas de données',
        no_data_available: 'Aucune donnée disponible',
        no_data_min_n: 'Pas de données (n < {n})',
        no_segmented_data: 'Aucune donnée segmentée disponible',
        calc_note: 'Note sur le calcul:',
        other_category: 'Autres',
        deviation_from_average: 'Écart par rapport à la moyenne (points de pourcentage)',
        pp_unit: 'pts',
        low_n_short: 'Faible effectif',
        too_few_data: 'Trop peu de données',
        partial_selection: 'Sélection partielle',
        total_label: 'Total',
        selection: 'Sélection',
    },
    
    en: {
        no_about_configured: 'No description is configured for this survey (settings.survey_metadata.about).',
        nps_note: 'NPS {nps} (promoters {promoters}%, passives {passives}%, detractors {detractors}%; n = {n})',
        scale_boxes: 'Top points: {top}; bottom points: {bottom}.',
        scale_mean: 'Mean',
        scale_top: 'Top {k}',
        scale_bottom: 'Bottom {k}',
        scale_note: 'Scale from {min} to {max}; answers outside the scale (e.g. "don\'t know") in brackets, not counted.',
        date_count: 'Count',
        date_range_note: 'n = {n}; from {first} to {last}; median {median}',
        nm_field: 'Field',
        nm_mean: 'Mean',
        nm_median: 'Median',
        nm_sd: 'SD',
        nm_min: 'Min',
        nm_max: 'Max',
        nm_share_of_total: 'Share of total',
        nm_sum_note: '{share}% of respondents (n = {n}) reach the total of {total}.',
        compare_by: 'Compare by',
        show_specific_clusters: 'Free text: show segment-specific clusters instead of overall clusters',
        // Header
        title: 'Survey',
        subtitle: 'Interactive Results Dashboard',
        
        // Filter
        filter: 'Filter',
        no_filter: 'No filter',
        all: 'All',
        
        // sample
        sample_size: 'Sample size',
        total: 'total',
        filtered: 'Filtered',
        insufficient_data: 'Insufficient data for analysis',
        insufficient_data_short: 'Too few responses for this combination',
        basis: 'Basis',
        
        // themes
        no_questions: 'No questions in this topic area',
        
        // Cluster-Details
        cluster_details: 'Show cluster details',
        segment_comparison: 'Compare by segments',
        jump_to: 'Jump to:',
        examples: 'Examples',
        
        // Freetext questions: mentions/persons
        mentions: 'mentions',
        persons: 'persons',
        from: 'from',
        
        // Navigation Modes
        view_all_questions: 'All Questions',
        more_questions: 'Further questions',
        view_by_themes: 'By Topics',
        view_my_profile: 'My selection',
        
        // Home / Overview
        survey_title: '',
        survey_subtitle: '',
        download_publication: 'Download Publication',
        start_exploration: 'Explore Results',
        participants: 'Participants',
        segValues: 'Groups',
        survey_period: 'Survey Period',
        about_survey: 'About the Survey',
        how_to_use: 'How to Use This Dashboard',
        nav_help_questions: 'Chronological overview of all questions',
        nav_help_themes: 'Questions grouped by topic areas',
        nav_help_correlations: 'Correlations between questions as heatmap or network',
        nav_help_segments: 'Differences between groups',
        nav_help_profile: 'Filter by the groups of the survey',
        
        // Summary
        summary_title: 'Summary of Results',
        summary_subtitle: 'Key findings of the survey',
        
        // Keywords
        
        // own selection (profile view)
        my_profile_settings: 'My Profile',
        profile_active: 'Profile filter active',
        of_total: 'of',
        responses: 'responses',
        freetext_clustered_separately: 'Clusters created separately',
        freetext_cluster_hint: 'The cluster categories were created separately for this segment and are not directly comparable to the overall results.',
        apply_filter: 'Apply filter',
        reset_filter: 'Reset',
        filter_fallback_hint: 'Insufficient data for this filter combination. Results are shown for a partial selection.',
        
        // groups
        
        // Chart-Export
        export: 'Export',
        export_chart: 'Export chart',
        
        // general
        loading: 'Loading...',
        error_title: 'Loading error',
        retry: 'Try again',
        no_segmented_results: 'Segmented results not available',
        no_segmented_results_hint: 'Overall results are shown.',
        no_segments_defined: 'No segmentation criteria are defined for this survey.',
        profile_info_multiselect: 'Select one or more options per category. Multiple selection is possible.',
        select_all: 'Select all',
        deselect_all: 'Deselect all',
        
        // Footer
        methodology: 'Methodology',
        imprint: 'Legal notice',
        contact: 'Contact',
        privacy: 'Privacy',
        
        // Segment-Labels
        
        // scale labels
        
        // Correlations
        view_correlations: 'Correlations',
        corr_view_cards: 'Overview',
        corr_view_heatmap: 'Heatmap',
        corr_view_network: 'Network',
        corr_total: 'Total correlations',
        corr_strong: 'Strong',
        corr_medium: 'Medium',
        corr_weak: 'Weak',
        possible_reading: 'Possible reading',
        group_differences_tab: 'Tested differences',
        group_differences_intro: 'Statistically supported differences between the groups: tested per answer option, item and topic, p values adjusted per question for the number of tests (Benjamini-Hochberg). Listed from a weak effect size on and, for shares, from a gap of {points} percentage points. A difference is not a cause; the "possible reading" is an interpretation, not a finding.',
        no_group_differences_data: 'No tested differences between groups available (run the pipeline with the segment comparison).',
        group_differences_without: 'Without a listed difference',
        group_differences_summary: 'Summary',
        adjusted_p: 'q (adjusted)',
        measure_cramers_v: "Cramér's V",
        measure_kendall_tau_b: "Kendall's τ-b",
        measure_epsilon_squared: 'ε²',
        filter_by_strength: 'Filter by strength:',
        correlations: 'Correlations',
        cross_theme_correlations: 'Cross-theme correlations',
        correlation_matrix: 'Correlation Matrix',
        correlation_network: 'Correlation Network',
        questions: 'Questions',
        no_correlations: 'No correlation data available',
        no_correlations_for_filter: 'No correlations found for this strength filter',
        
        // segment comparison
        view_segment_comparison: 'Group comparison',
        segment_clusters_tab: 'Segment-specific themes',
        segment_deviations_tab: 'Deviations from the average',
        no_segment_clusters_data: 'No segment-specific clusters available (set enable_specific_clusters in the configuration).',
        deviation_threshold: 'Deviation threshold:',
        no_segment_cluster_data: 'No free-text clusters with group data available.',
        no_significant_deviations: 'No deviations above this threshold (percentage points, no statistical test).',
        respondents: 'Respondents',
        more_frequent: 'More frequent',
        less_frequent: 'Less frequent',
        no_summary_configured: 'No summary is configured for this survey (settings.survey_metadata.summary).',
        // texts of the views and charts
        no_data: 'No data',
        no_data_available: 'No data available',
        no_data_min_n: 'No data (n < {n})',
        no_segmented_data: 'No segmented data available',
        calc_note: 'Note on calculation:',
        other_category: 'Other',
        deviation_from_average: 'Deviation from average (percentage points)',
        pp_unit: 'pp',
        low_n_short: 'Low sample size',
        too_few_data: 'Too few data',
        partial_selection: 'Partial selection',
        total_label: 'Total',
        selection: 'Selection',
    },
    it: {
        no_about_configured: 'Nessuna descrizione configurata per questa indagine (settings.survey_metadata.about).',
        nps_note: 'NPS {nps} (promotori {promoters}%, passivi {passives}%, detrattori {detractors}%; n = {n})',
        scale_boxes: 'Punti superiori: {top}; punti inferiori: {bottom}.',
        scale_mean: 'Media',
        scale_top: 'Top {k}',
        scale_bottom: 'Bottom {k}',
        scale_note: 'Scala da {min} a {max}; risposte fuori scala (ad es. «non so») tra parentesi, non conteggiate.',
        date_count: 'Numero',
        date_range_note: 'n = {n}; dal {first} al {last}; mediana {median}',
        nm_field: 'Campo',
        nm_mean: 'Media',
        nm_median: 'Mediana',
        nm_sd: 'DS',
        nm_min: 'Min',
        nm_max: 'Max',
        nm_share_of_total: 'Quota del totale',
        nm_sum_note: 'Il {share}% dei rispondenti (n = {n}) raggiunge il totale di {total}.',
        // Italian; texts specific to one survey (title, summary, about) fall back to English
        compare_by: 'Confronta per',
        show_specific_clusters: 'Testo libero: mostra i cluster specifici del segmento invece dei cluster complessivi',
        subtitle: 'Dashboard interattiva dei risultati',
        filter: 'Filtro',
        no_filter: 'Nessun filtro',
        all: 'Tutti',
        sample_size: 'Dimensione del campione',
        total: 'totale',
        filtered: 'Filtrato',
        insufficient_data: 'Dati insufficienti per l\'analisi',
        insufficient_data_short: 'Troppe poche risposte per questa combinazione',
        basis: 'Base',
        no_questions: 'Nessuna domanda in quest\'area tematica',
        cluster_details: 'Mostra dettagli dei cluster',
        segment_comparison: 'Confronta per segmenti',
        jump_to: 'Vai a:',
        examples: 'Esempi',
        mentions: 'menzioni',
        persons: 'persone',
        from: 'da',
        view_all_questions: 'Tutte le domande',
        more_questions: 'Altre domande',
        view_by_themes: 'Per temi',
        view_my_profile: 'La mia selezione',
        download_publication: 'Scarica la pubblicazione',
        start_exploration: 'Esplora i risultati',
        participants: 'Partecipanti',
        segValues: 'Gruppi',
        survey_period: 'Periodo dell\'indagine',
        about_survey: 'Informazioni sull\'indagine',
        how_to_use: 'Come usare questa dashboard',
        nav_help_questions: 'Panoramica cronologica di tutte le domande',
        nav_help_themes: 'Domande raggruppate per aree tematiche',
        nav_help_correlations: 'Correlazioni tra le domande come mappa di calore o rete',
        nav_help_segments: 'Differenze tra gruppi',
        nav_help_profile: 'Filtra per i gruppi dell\'indagine',
        summary_title: 'Sintesi dei risultati',
        my_profile_settings: 'Il mio profilo',
        profile_active: 'Filtro del profilo attivo',
        of_total: 'di',
        responses: 'risposte',
        freetext_clustered_separately: 'Cluster creati separatamente',
        freetext_cluster_hint: 'Le categorie dei cluster sono state create separatamente per questo segmento e non sono direttamente confrontabili con i risultati complessivi.',
        apply_filter: 'Applica filtro',
        reset_filter: 'Reimposta',
        filter_fallback_hint: 'Dati insufficienti per questa combinazione di filtri. I risultati sono mostrati per una selezione parziale.',
        export: 'Esporta',
        export_chart: 'Esporta grafico',
        loading: 'Caricamento...',
        error_title: 'Errore di caricamento',
        retry: 'Riprova',
        no_segmented_results: 'Risultati segmentati non disponibili',
        no_segmented_results_hint: 'Vengono mostrati i risultati complessivi.',
        no_segments_defined: 'Per questa indagine non sono definiti criteri di segmentazione.',
        profile_info_multiselect: 'Seleziona una o più opzioni per categoria. È possibile la selezione multipla.',
        select_all: 'Seleziona tutto',
        deselect_all: 'Deseleziona tutto',
        methodology: 'Metodologia',
        imprint: 'Note legali',
        contact: 'Contatti',
        privacy: 'Privacy',
        view_correlations: 'Correlazioni',
        corr_view_cards: 'Panoramica',
        corr_view_heatmap: 'Mappa di calore',
        corr_view_network: 'Rete',
        corr_total: 'Correlazioni totali',
        corr_strong: 'Forte',
        corr_medium: 'Media',
        corr_weak: 'Debole',
        possible_reading: 'Possibile lettura',
        group_differences_tab: 'Differenze testate',
        group_differences_intro: 'Differenze statisticamente supportate tra i gruppi: testate per opzione di risposta, item e tema, valori p corretti per domanda in base al numero di test (Benjamini-Hochberg). Riportate a partire da un effetto debole e, per le quote, da una distanza di {points} punti percentuali. Una differenza non è una causa; la «possibile lettura» è un\'interpretazione, non un risultato.',
        no_group_differences_data: 'Nessuna differenza testata tra i gruppi disponibile (eseguire la pipeline con il confronto dei segmenti).',
        group_differences_without: 'Senza differenza riportata',
        group_differences_summary: 'Sintesi',
        adjusted_p: 'q (corretto)',
        measure_cramers_v: 'V di Cramér',
        measure_kendall_tau_b: 'τ-b di Kendall',
        measure_epsilon_squared: 'ε²',
        filter_by_strength: 'Filtra per intensità:',
        correlations: 'Correlazioni',
        cross_theme_correlations: 'Correlazioni tra temi',
        correlation_matrix: 'Matrice di correlazione',
        correlation_network: 'Rete di correlazioni',
        questions: 'Domande',
        no_correlations: 'Nessun dato di correlazione disponibile',
        no_correlations_for_filter: 'Nessuna correlazione trovata per questo filtro di intensità',
        view_segment_comparison: 'Confronto tra gruppi',
        segment_clusters_tab: 'Temi specifici del segmento',
        segment_deviations_tab: 'Scostamenti dalla media',
        no_segment_clusters_data: 'Nessun cluster specifico del segmento disponibile (impostare enable_specific_clusters nella configurazione).',
        deviation_threshold: 'Soglia di scostamento:',
        no_segment_cluster_data: 'Nessun cluster di testo libero con dati di gruppo disponibile.',
        no_significant_deviations: 'Nessuno scostamento oltre questa soglia (punti percentuali, nessun test statistico).',
        respondents: 'Rispondenti',
        more_frequent: 'Più frequente',
        less_frequent: 'Meno frequente',
        title: 'Indagine',
        survey_title: '',
        survey_subtitle: '',
        summary_subtitle: 'I principali risultati dell\'indagine',
        no_summary_configured: 'Nessuna sintesi configurata per questa indagine (settings.survey_metadata.summary).',
        // texts of the views and charts
        no_data: 'Nessun dato',
        no_data_available: 'Nessun dato disponibile',
        no_data_min_n: 'Nessun dato (n < {n})',
        no_segmented_data: 'Nessun dato segmentato disponibile',
        calc_note: 'Nota sul calcolo:',
        other_category: 'Altro',
        deviation_from_average: 'Scostamento dalla media (punti percentuali)',
        pp_unit: 'pp',
        low_n_short: 'Campione ridotto',
        too_few_data: 'Dati insufficienti',
        partial_selection: 'Selezione parziale',
        total_label: 'Totale',
        selection: 'Selezione',
    },
};

/**
 * Translation function
 * @param {string} key - translation key (e.g. 'filter' or 'segments.role')
 * @param {string} lang - language code (de, fr, en, it)
 * @returns {string} translated text
 */
/**
 * Text of a multilingual value {lang: text} in ``lang``; falls back to the
 * survey's source language, English, German, then any language. Returns
 * undefined if nothing is there, so callers can chain their own default.
 */
function pickLang(value, lang) {
    if (!value || typeof value !== 'object') return value || undefined;
    const src = (typeof window !== 'undefined' && window.__surveySourceLanguage) || undefined;
    for (const code of [lang, src, 'en', 'de']) {
        if (code && value[code]) return value[code];
    }
    return Object.values(value).find(v => v) || undefined;
}
if (typeof window !== 'undefined') window.pickLang = pickLang;

// Global for app.js (a top-level const is not a property of window)
if (typeof window !== 'undefined') window.i18n = i18n;

function t(key, lang = 'de') {
    // The language, then English, then German (the original texts)
    for (const code of [lang, 'en', 'de']) {
        let value = i18n[code];
        for (const k of key.split('.')) {
            value = (value && typeof value === 'object' && k in value) ? value[k] : undefined;
        }
        if (typeof value === 'string') return value;
    }
    return key;
}


// export for ES6 modules (optional)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { i18n, t };
}
