# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Free-text handler: LLM clustering of open answers.

Clusters free-text answers automatically with an LLM and allows an interactive
refinement of the clusters.

Two modes:
- raw: clustering of the original answers (one per respondent)
- items: clustering of extracted single items (several per respondent possible)
"""

import json
import logging
from typing import Optional, List, Dict, Any, Set, Union
from dataclasses import dataclass, field

import pandas as pd
import matplotlib.pyplot as plt

from .base import SingleColumnHandler, AnalysisResult
from utils.languages import text as _text

logger = logging.getLogger(__name__)

# Several text fields per question: id of a mention = data row * factor + field index
FIELD_ID_FACTOR = 1000


def _localized_text(value, lang: str) -> str:
    if not isinstance(value, dict):
        return str(value)
    from utils import prompts
    from utils.languages import localized
    return str(localized(value, lang, prompts.source_language()) or "")


@dataclass
class ClusterInfo:
    """Information on a cluster."""
    name: Union[str, Dict[str, str]]  # string or {lang: text} dict
    description: Union[str, Dict[str, str]]  # string or {lang: text} dict
    answer_ids: List[int]  # ids of the items (item mode) or answers (raw mode)
    examples: List[str]  # original examples (source language)
    examples_translated: Dict[str, List[str]] = None  # {lang: [...]}
    response_ids: List[int] = None  # unique respondent ids (only relevant in item mode)
    count: int = 0  # number of items
    unique_respondents: int = 0  # number of unique respondents
    
    def __post_init__(self):
        self.count = len(self.answer_ids)
        if self.examples_translated is None:
            # Original examples are in the source language; other languages are added by translation
            from utils.prompts import source_language
            self.examples_translated = {source_language(): self.examples[:]}
        # compute the unique respondents if response_ids are available
        if self.response_ids:
            self.unique_respondents = len(set(self.response_ids))
        else:
            # fallback: in raw mode answer_ids = response_ids
            self.unique_respondents = self.count
    
    def get_name(self, lang: str = "de") -> str:
        """Name in the requested language; fallback: source language, English, any."""
        return _localized_text(self.name, lang)
    
    def get_description(self, lang: str = "de") -> str:
        """Description in the requested language; fallback as for get_name."""
        return _localized_text(self.description, lang)
    
    def get_examples(self, lang: str = "de") -> List[str]:
        """The examples in the requested language."""
        if self.examples_translated and lang in self.examples_translated:
            translated = self.examples_translated.get(lang, [])
            if translated:
                return translated
        # fallback: the original examples
        return self.examples


@dataclass
class ClusteringResult:
    """Result of a clustering."""
    clusters: List[ClusterInfo]
    unclustered: List[int] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def total_clustered(self) -> int:
        return sum(c.count for c in self.clusters)
    
    def get_cluster_names(self, lang: str = "de") -> List[str]:
        """Cluster names in the requested language."""
        return [c.get_name(lang) for c in self.clusters]


class FreetextHandler(SingleColumnHandler):
    """
    Handler for free-text questions with LLM clustering.
    
    This handler:
    1. collects all free-text answers (or extracted items)
    2. sends them to an LLM to form clusters
    3. allows an interactive refinement
    4. creates charts and exports
    
    Modes:
    - raw: original answers (default)
    - items: single items from the item extraction
    """
    
    handler_type = "freetext"
    supported_charts = ["bar_horizontal", "treemap"]
    
    # ------------------------------------------------------------------
    # OPTIONAL: handler-specific default summary schema
    # ------------------------------------------------------------------
    # The default schema of the BaseHandler is used (three sections:
    # description / interpretation / segments_text), which keeps the previous
    # behaviour exactly.
    #
    # A free-text-specific default schema (e.g. with its own cluster_summary
    # section) could be activated here, OR set per survey in questions.yaml under
    # settings.summary_schema_overrides.freetext, which is preferable, as it keeps
    # the previous reports reproducible.
    #
    # @classmethod
    # def default_summary_schema(cls):
    #     from utils.summary_schema import SummarySchema, Section
    #     return SummarySchema(
    #         schema_id='freetext_clusters_default',
    #         handler_type='freetext',
    #         sections=[
    #             Section(id='overview',
    #                     label={'de': 'Übersicht', 'fr': 'Aperçu', 'en': 'Overview'},
    #                     prompt_instruction="Factual description of the answer structure."),
    #             Section(id='cluster_summary',
    #                     label={'de': 'Themen-Cluster', 'fr': 'Clusters thématiques',
    #                            'en': 'Topic clusters'},
    #                     prompt_instruction="Describe the most important topic clusters.",
    #                     conditional='has_clusters'),
    #             Section(id='examples',
    #                     label={'de': 'Beispiele', 'fr': 'Exemples', 'en': 'Examples'},
    #                     prompt_instruction="2-3 illustrative example answers."),
    #             Section(id='segments_text',
    #                     label={'de': 'Segmentunterschiede', ...},
    #                     prompt_instruction="...",
    #                     conditional='has_segmentation_significant'),
    #         ],
    #     )
    
    def __init__(self, config: dict):
        """Initialises the FreetextHandler."""
        super().__init__(config)
        self.clustering_result: Optional[ClusteringResult] = None
        self._answers_cache: Optional[pd.DataFrame] = None
        self._item_to_response: Dict[int, int] = {}  # Mapping: item_id → row_index
        
        # clustering parameters from the configuration
        self.min_cluster_size = config.get("clustering", {}).get("min_cluster_size", 3)
        self.max_clusters = config.get("clustering", {}).get("max_clusters", 8)
        self.clustering_mode = config.get("clustering", {}).get("mode", "auto")  # 'auto', 'raw', 'items'
        self.clustering_hint = config.get("clustering", {}).get("hint", None)
        self.max_cluster_pct = config.get("clustering", {}).get("max_cluster_pct", None)  # None = dynamic default
        
        # item extraction enabled?
        self.use_items = config.get("item_extraction", {}).get("enabled", False)
        self._analysis_mode = "raw"  # 'raw' or 'items'
    
    def analyze(
        self,
        df: pd.DataFrame,
        lang: str = "de",
        use_cached_clustering: bool = True,
        db=None,
        import_id: int = None,
        force_items: bool = None
    ) -> AnalysisResult:
        """
        Analyses a free-text question.
        
        Args:
            df: DataFrame with the survey data
            lang: language
            use_cached_clustering: whether a cached clustering is used
            db: database instance (for item mode)
            import_id: import id (for item mode)
            force_items: force item mode (overrides the configuration)
        
        Returns:
            AnalysisResult with cluster information
        """
        column = self.get_column()
        
        if not column or column not in df.columns:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=[f"Column not found: {column}"]
            )
        
        # item mode: do extracted items exist?
        # clustering.mode: 'raw' forces raw mode even if items exist
        if db is not None and import_id is not None:
            # the configured mode overrides the automatic detection
            if self.clustering_mode == 'raw':
                logger.info(f"Raw mode forced by the configuration for {self.question_id}")
            elif force_items is False:
                # raw mode forced explicitly (API parameter)
                pass
            elif self._extraction_complete(db, import_id, df):
                # items exist -> use them automatically
                logger.info(f"Extracted items found for {self.question_id}, using item mode")
                # Pass the rows of df so that a segment-filtered DataFrame
                # restricts the items as well; the items come from the DB
                # and would otherwise always cover the whole survey.
                return self._analyze_items(
                    db, import_id, column, lang, use_cached_clustering,
                    row_indices=set(df.index),
                    all_columns=len(self._text_columns(df)) > 1,
                )
            else:
                logger.info(f"No extracted items for {self.question_id}, using raw mode")
        
        # Several text fields per question ("up to five reasons" as five fields,
        # a form): every filled field is a mention of the respondent
        if len(self._text_columns(df)) > 1:
            return self._analyze_fields(df, self._text_columns(df), lang, use_cached_clustering)
        # Standard: Raw-Modus
        return self._analyze_raw(df, column, lang, use_cached_clustering)
    
    def _text_columns(self, df: pd.DataFrame) -> List[str]:
        return [c for c in self.columns if c in df.columns]

    def _extraction_complete(self, db, import_id: int, df: pd.DataFrame) -> bool:
        """
        Items exist for every text field that has answers. Items of only some
        fields (e.g. extracted before all fields were covered) would silently
        drop the other fields, so the question is then analysed from the raw
        fields with a warning until the items are extracted again.
        """
        filled = [c for c in self._text_columns(df) if df[c].notna().any()]
        extracted = [c for c in filled if db.has_extraction(import_id, self.question_id, c)]
        if extracted and len(extracted) < len(filled):
            missing = [c for c in filled if c not in extracted]
            logger.warning(f"{self.question_id}: items only for {extracted}, not for {missing}; "
                           f"analysing the fields without item extraction (run the item extraction again)")
            return False
        return bool(extracted)
    
    def _analyze_fields(self, df: pd.DataFrame, columns: List[str], lang: str,
                        use_cached_clustering: bool) -> AnalysisResult:
        """
        All text fields of the question together: each filled field is one
        mention (item) of its respondent; clustered over all mentions,
        counted as mentions and respondents like the item mode.
        """
        self._analysis_mode = "fields"
        rows = []
        for col_index, col in enumerate(columns):
            values = df[col]
            for idx, value in values[values.notna()].items():
                text_value = str(value).strip()
                if text_value:
                    # id of a mention: data row and field (FIELD_ID_FACTOR), so the
                    # dashboard finds the respondent of every mention
                    rows.append({'original_index': int(idx) * FIELD_ID_FACTOR + col_index, 'answer': text_value,
                                 'row_index': idx, 'extraction_method': 'field', 'column': col})
        if not rows:
            return AnalysisResult(question_id=self.question_id, n=0, data={}, warnings=["No valid answers"])
        items_df = pd.DataFrame(rows)
        n_items, n_responses = len(items_df), items_df['row_index'].nunique()
        self._item_to_response = dict(zip(items_df['original_index'], items_df['row_index']))
        self._answers_cache = items_df
        if not use_cached_clustering or self.clustering_result is None:
            self.clustering_result = self._perform_clustering(items_df[['original_index', 'answer']], lang)
        self._enrich_clusters_with_response_ids()
        result = self._build_result(n_responses, columns[0], lang, "items")
        result.metadata.update({
            'analysis_mode': 'fields',
            'total_items': n_items,
            'unique_responses': n_responses,
            'avg_items_per_response': round(n_items / n_responses, 2) if n_responses else 0,
            'extraction_methods': {'field': n_items},
            'text_columns': list(columns),
        })
        result.description = self._generate_item_description(result.data, n_items, n_responses, lang)
        return result
    
    def _analyze_raw(
        self,
        df: pd.DataFrame,
        column: str,
        lang: str,
        use_cached_clustering: bool
    ) -> AnalysisResult:
        """Analyses in raw mode (original answers)."""
        self._analysis_mode = "raw"
        
        # filter the valid answers
        valid_df = df[df[column].notna() & (df[column].str.strip() != "")].copy()
        n = len(valid_df)
        
        if n == 0:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No valid answers"]
            )
        
        # cache the answers with their ids
        self._answers_cache = valid_df[[column]].reset_index()
        self._answers_cache.columns = ['original_index', 'answer']
        
        # cluster, or use the cache
        if not use_cached_clustering or self.clustering_result is None:
            self.clustering_result = self._perform_clustering(
                self._answers_cache,
                lang
            )
        
        return self._build_result(n, column, lang, "raw")
    
    def _analyze_items(
        self,
        db,
        import_id: int,
        column: str,
        lang: str,
        use_cached_clustering: bool,
        row_indices: Optional[Set[int]] = None,
        all_columns: bool = False,
    ) -> AnalysisResult:
        """
        Analyses in item mode (the extracted single items).

        row_indices: DataFrame rows to include. Items are matched through
        raw_responses.row_index, which is the DataFrame index at import time;
        survey data is imported with a positional index, so both agree.
        None keeps all items.
        """
        self._analysis_mode = "items"
        
        # load the items from the database
        items = db.get_items_for_question(import_id, self.question_id, None if all_columns else column)
        if row_indices is not None:
            unmatched = sum(1 for it in items if it.get('raw_row_index') is None)
            if unmatched:
                logger.warning(
                    f"{self.question_id}: {unmatched} items without row_index, "
                    f"not considered when filtering"
                )
            items = [it for it in items if it.get('row_index') in row_indices]
        
        if not items:
            return AnalysisResult(
                question_id=self.question_id,
                n=0,
                data={},
                warnings=["No extracted items found"]
            )
        
        # items into a DataFrame
        # The respondent of an item is its data row (raw_responses.row_index =
        # DataFrame index; response_items.row_index stores that row), so that
        # cluster memberships match the data for segment tests and associations.
        items_df = pd.DataFrame([
            {
                'original_index': item['item_id'],
                'answer': item['extracted_text'],
                'row_index': item['row_index'],
                'extraction_method': item['extraction_method']
            }
            for item in items
        ])
        
        n_items = len(items_df)
        n_responses = items_df['row_index'].nunique()
        
        # create the item id → row mapping
        self._item_to_response = dict(zip(items_df['original_index'], items_df['row_index']))
        
        # cache the items
        self._answers_cache = items_df
        
        # run the clustering
        if not use_cached_clustering or self.clustering_result is None:
            self.clustering_result = self._perform_clustering(
                items_df[['original_index', 'answer']],
                lang
            )
        
        # add the response_ids to the clusters (for a correct count of respondents)
        self._enrich_clusters_with_response_ids()
        
        # result with item statistics; n is unique_responses
        result = self._build_result(n_responses, column, lang, "items")
        
        # additional metadata for item mode
        result.metadata.update({
            'analysis_mode': 'items',
            'total_items': n_items,
            'unique_responses': n_responses,
            'avg_items_per_response': round(n_items / n_responses, 2) if n_responses else 0,
            'extraction_methods': items_df['extraction_method'].value_counts().to_dict()
        })
        
        # adapt the description
        result.description = self._generate_item_description(
            result.data, n_items, n_responses, lang
        )
        
        return result
    
    def _enrich_clusters_with_response_ids(self):
        """Adds the response ids to each cluster."""
        if not self.clustering_result or not self._item_to_response:
            logger.warning("Cluster enrichment skipped: no clusters or no item mapping")
            return
        
        logger.debug(f"Item-to-response mapping has {len(self._item_to_response)} entries")
        
        total_items_in_clusters = 0
        total_unique_respondents = 0
        
        for cluster in self.clustering_result.clusters:
            # look up the row_index of every item_id
            response_ids = [
                self._item_to_response.get(item_id)
                for item_id in cluster.answer_ids
                if item_id in self._item_to_response
            ]
            
            # how many ids were not found?
            missing_ids = [aid for aid in cluster.answer_ids if aid not in self._item_to_response]
            if missing_ids:
                logger.warning(
                    f"Cluster '{cluster.get_name(self._source_language())}': {len(missing_ids)} of {len(cluster.answer_ids)} "
                    f"item IDs not found in the mapping"
                )
            
            cluster.response_ids = [rid for rid in response_ids if rid is not None]
            cluster.unique_respondents = len(set(cluster.response_ids))
            
            total_items_in_clusters += len(cluster.answer_ids)
            total_unique_respondents += cluster.unique_respondents
            
            logger.debug(
                f"Cluster '{cluster.get_name(self._source_language())}': {len(cluster.answer_ids)} Items → "
                f"{cluster.unique_respondents} unique Respondenten"
            )
        
        logger.info(
            f"Cluster enrichment: {total_items_in_clusters} items in {len(self.clustering_result.clusters)} clusters, "
            f"{total_unique_respondents} respondent mentions in total"
        )
    
    def _build_result(
        self,
        n: int,
        column: str,
        lang: str,
        mode: str
    ) -> AnalysisResult:
        """Builds the AnalysisResult from the clustering result.
        
        Args:
            n: number of unique respondents (item mode) or answers (raw mode)
            column: column name
            lang: language
            mode: 'raw' or 'items'
        """
        # item mode: count unique_respondents
        # raw mode: count = unique_respondents (1 answer = 1 person)
        use_respondent_counts = (mode == "items")
        
        # cluster counts:
        # item mode: number of unique respondents per cluster
        # raw mode: number of answers per cluster (= respondents)
        # keys: cluster names in the source language, made unique so two
        # clusters with the same name do not overwrite each other's count
        cluster_counts = {}
        for c in self.clustering_result.clusters:
            key = self._unique_key(c, cluster_counts)
            cluster_counts[key] = c.unique_respondents if use_respondent_counts else c.count
        
        # sort by size
        cluster_counts = dict(sorted(
            cluster_counts.items(), 
            key=lambda x: x[1], 
            reverse=True
        ))
        
        # percentages: share of the respondents who mentioned this topic
        percentages = {
            name: round(count / n * 100, 1) if n > 0 else 0
            for name, count in cluster_counts.items()
        }
        
        return AnalysisResult(
            question_id=self.question_id,
            n=n,
            data=cluster_counts,
            percentages=percentages,
            clusters=self.clustering_result,
            description=self.generate_description_text(cluster_counts, n, lang),
            metadata={
                "column": column,
                "analysis_mode": mode,
                "unclustered_count": len(self.clustering_result.unclustered),
                "cluster_details": [
                    {
                        # keep the multilingual names/descriptions
                        # plain texts are in the source language (other languages fall back to it)
                        "name": c.name if isinstance(c.name, dict) else {self._source_language(): c.name},
                        "description": c.description if isinstance(c.description, dict) else {self._source_language(): c.description},
                        "count": c.count,  # number of items (for transparency)
                        "unique_respondents": c.unique_respondents,  # number of persons
                        "examples": c.examples[:3],
                        "examples_translated": c.examples_translated if c.examples_translated else {self._source_language(): c.examples[:3]},
                        "answer_ids": c.answer_ids  # ids of the items/answers in this cluster
                    }
                    for c in self.clustering_result.clusters
                ]
            }
        )
    
    def _generate_item_description(
        self,
        cluster_counts: dict,
        n_items: int,
        n_responses: int,
        lang: str
    ) -> str:
        """Writes the description text for item mode."""
        from utils.languages import text
        return text("handler.freetext.items", lang, n_items=n_items, n_responses=n_responses,
                    n_clusters=len(cluster_counts))
    
    def _perform_clustering(
        self,
        answers_df: pd.DataFrame,
        lang: str
    ) -> ClusteringResult:
        """
        Runs the LLM clustering.
        
        Args:
            answers_df: DataFrame with the columns ['original_index', 'answer']
            lang: language
        
        Returns:
            ClusteringResult
        """
        from utils import llm as llm_module
        
        try:
            # the shared client (utils.llm.get_llm_client): one connection pool, and
            # tests and checks can provide their own client
            llm_client = llm_module.get_llm_client()
            
            # prepare the answers for the LLM
            answers_list = []
            for idx, row in answers_df.iterrows():
                answers_list.append({
                    "id": int(row['original_index']),
                    "text": str(row['answer'])
                })
            
            # question title for context
            question_title = self.get_title(lang)
            
            # call the LLM clustering
            result = llm_client.cluster_responses(
                question=question_title,
                responses=answers_list,
                min_cluster_size=self.min_cluster_size,
                max_clusters=self.max_clusters,
                hint=self.clustering_hint,
                max_cluster_pct=self.max_cluster_pct
            )
            
            # parse the result
            clusters = []
            all_clustered_ids = set()
            
            for cluster_data in result.get("clusters", []):
                cluster = ClusterInfo(
                    name=cluster_data.get("name") or _text("word.unnamed", lang),
                    description=cluster_data.get("description", ""),
                    answer_ids=cluster_data.get("answer_ids", []),
                    examples=cluster_data.get("examples", []),
                    examples_translated=cluster_data.get("examples_translated", None)
                )
                clusters.append(cluster)
                all_clustered_ids.update(cluster.answer_ids)
            
            # find the ids that were not assigned
            all_ids = set(answers_df['original_index'].tolist())
            unclustered = list(all_ids - all_clustered_ids)
            
            return ClusteringResult(
                clusters=clusters,
                unclustered=unclustered,
                metadata={"llm_raw_result": result}
            )
            
        except Exception as e:
            logger.error(f"Error in the clustering: {e}")
            # fallback: all answers in one cluster
            from utils import prompts
            from utils.languages import get_output_languages, text
            langs = list(dict.fromkeys([prompts.source_language(),
                                        *get_output_languages(prompts.current_config())]))
            all_ids = answers_df['original_index'].tolist()
            fallback_examples = answers_df['answer'].head(3).tolist()
            return ClusteringResult(
                clusters=[
                    ClusterInfo(
                        name={code: text("clustering.all_responses", code) for code in langs},
                        description={code: text("clustering.failed", code) for code in langs},
                        answer_ids=all_ids,
                        examples=fallback_examples,
                        examples_translated={code: fallback_examples for code in langs}
                    )
                ],
                metadata={"error": str(e)}
            )
    
    def generate_description_text(
        self,
        cluster_counts: dict,
        n: int,
        lang: str
    ) -> str:
        """Writes a description text with translated cluster names."""
        if not cluster_counts:
            return ""
        
        num_clusters = len(cluster_counts)
        top_cluster_src = max(cluster_counts, key=lambda k: cluster_counts[k])  # largest; name in the source language
        top_count = cluster_counts[top_cluster_src]
        top_pct = top_count / n * 100 if n > 0 else 0
        
        # translated cluster names from clustering_result
        top_cluster = top_cluster_src
        if self.clustering_result:
            for c in self.clustering_result.clusters:
                if c.get_name(self._source_language()) == top_cluster_src:
                    top_cluster = c.get_name(lang)
                    break
        
        from utils.languages import text
        # single cluster: special text (no "grouped in 1 cluster")
        if num_clusters == 1:
            return text("handler.freetext.single_cluster", lang, n=n, cluster=top_cluster)
        return text("handler.freetext.clusters", lang, n=n, n_clusters=num_clusters, cluster=top_cluster,
                    count=top_count, pct=top_pct)
    
    def create_chart(
        self,
        result: AnalysisResult,
        chart_type: str,
        lang: str = "de",
        **kwargs
    ) -> plt.Figure:
        """
        Creates a chart with translated cluster names.
        
        Args:
            result: analysis result
            chart_type: chart type
            lang: language
        
        Returns:
            matplotlib figure
        """
        from utils.charts import ChartGenerator
        
        generator = ChartGenerator(kwargs.get('colors'))
        title = self.get_title(lang)
        colors = self.get_colors()
        
        # get the translated cluster names
        cluster_details = result.metadata.get('cluster_details', [])
        name_mapping = {}
        if self.clustering_result:
            seen: dict = {}
            for c in self.clustering_result.clusters:
                key = self._unique_key(c, seen)
                seen[key] = True
                suffix = key[len(c.get_name(self._source_language())):]
                name_mapping[key] = c.get_name(lang) + suffix
        
        # data with translated names
        translated_data = {
            name_mapping.get(k, k): v for k, v in result.data.items()
        }
        
        # build the n information (special case: item mode)
        if result.metadata.get('analysis_mode') in ('items', 'fields'):
            n_items = result.metadata.get('total_items', result.n)
            n_responses = result.metadata.get('unique_responses', result.n)
            if n_items != n_responses:
                from utils.languages import text
                n_info = text("handler.freetext.chart_items", lang, n_items=n_items, n_responses=n_responses)
            else:
                n_info = f"n={n_responses}"
        else:
            n_info = f"n={result.n}"
        
        if chart_type == "treemap":
            return generator.treemap(
                data=translated_data,
                title=title,
                colors=colors,
                lang=lang
            )
        else:
            # default: horizontal bars
            return generator.bar_horizontal(
                data=translated_data,
                title=title,
                colors=colors,
                lang=lang,
                total=result.n,
                n_info=n_info
            )
    
    @staticmethod
    def _source_language() -> str:
        from utils import prompts
        return prompts.source_language()

    def _unique_key(self, cluster, taken) -> str:
        """Source-language name of a cluster, with " (2)", " (3)" … if already taken."""
        base = cluster.get_name(self._source_language())
        key, i = base, 2
        while key in taken:
            key, i = f"{base} ({i})", i + 1
        return key

    # === interactive cluster refinement ===
    
    def merge_clusters(self, cluster_names: List[str], new_name: str) -> bool:
        """
        Merges several clusters.
        
        Args:
            cluster_names: names of the clusters to merge
            new_name: name of the new cluster
        
        Returns:
            True on success
        """
        if self.clustering_result is None:
            return False
        
        # find the cluster
        clusters_to_merge = [
            c for c in self.clustering_result.clusters
            if c.name in cluster_names
        ]
        
        if len(clusters_to_merge) < 2:
            return False
        
        # create the new cluster info
        merged_ids = []
        merged_examples = []
        merged_descriptions = []
        
        for c in clusters_to_merge:
            merged_ids.extend(c.answer_ids)
            merged_examples.extend(c.examples)
            merged_descriptions.append(c.description)
        
        new_cluster = ClusterInfo(
            name=new_name,
            description=" / ".join(merged_descriptions),
            answer_ids=merged_ids,
            examples=merged_examples[:5]
        )
        
        # remove the old clusters and add the new one
        self.clustering_result.clusters = [
            c for c in self.clustering_result.clusters
            if c.name not in cluster_names
        ]
        self.clustering_result.clusters.append(new_cluster)
        
        logger.info(f"Clusters merged: {cluster_names} -> {new_name}")
        return True
    
    def rename_cluster(self, old_name: str, new_name: str) -> bool:
        """
        Renames a cluster.
        
        Args:
            old_name: old name
            new_name: new name
        
        Returns:
            True on success
        """
        if self.clustering_result is None:
            return False
        
        for cluster in self.clustering_result.clusters:
            if cluster.name == old_name:
                cluster.name = new_name
                logger.info(f"Cluster renamed: {old_name} -> {new_name}")
                return True
        
        return False
    
    def move_answer(self, answer_id: int, target_cluster: str) -> bool:
        """
        Moves an answer into another cluster.
        
        Args:
            answer_id: id of the answer
            target_cluster: name of the target cluster
        
        Returns:
            True on success
        """
        if self.clustering_result is None:
            return False
        
        # remove from all clusters
        for cluster in self.clustering_result.clusters:
            if answer_id in cluster.answer_ids:
                cluster.answer_ids.remove(answer_id)
                cluster.count = len(cluster.answer_ids)
        
        # remove from unclustered
        if answer_id in self.clustering_result.unclustered:
            self.clustering_result.unclustered.remove(answer_id)
        
        # add to the target cluster
        for cluster in self.clustering_result.clusters:
            if cluster.name == target_cluster:
                cluster.answer_ids.append(answer_id)
                cluster.count = len(cluster.answer_ids)
                logger.info(f"Answer {answer_id} moved to {target_cluster}")
                return True
        
        return False
    
    def split_cluster(
        self,
        cluster_name: str,
        answer_ids_group1: List[int],
        new_name_1: str,
        new_name_2: str
    ) -> bool:
        """
        Splits a cluster in two.
        
        Args:
            cluster_name: name of the cluster to split
            answer_ids_group1: ids for the first new cluster
            new_name_1: name of group 1
            new_name_2: name of group 2 (the rest)
        
        Returns:
            True on success
        """
        if self.clustering_result is None:
            return False
        
        # find the original cluster
        original = None
        for c in self.clustering_result.clusters:
            if c.name == cluster_name:
                original = c
                break
        
        if original is None:
            return False
        
        # split the ids
        group1_ids = [aid for aid in answer_ids_group1 if aid in original.answer_ids]
        group2_ids = [aid for aid in original.answer_ids if aid not in group1_ids]
        
        if not group1_ids or not group2_ids:
            return False
        
        # get examples from the cache
        examples1 = self._get_examples_for_ids(group1_ids, 3)
        examples2 = self._get_examples_for_ids(group2_ids, 3)
        
        # create the new clusters
        cluster1 = ClusterInfo(
            name=new_name_1,
            description=_part_of(original.description),
            answer_ids=group1_ids,
            examples=examples1
        )
        
        cluster2 = ClusterInfo(
            name=new_name_2,
            description=_part_of(original.description),
            answer_ids=group2_ids,
            examples=examples2
        )
        
        # remove the original and add the new ones
        self.clustering_result.clusters.remove(original)
        self.clustering_result.clusters.extend([cluster1, cluster2])
        
        logger.info(f"Cluster split: {cluster_name} -> {new_name_1}, {new_name_2}")
        return True
    
    def _get_examples_for_ids(self, ids: List[int], max_examples: int = 3) -> List[str]:
        """Gets example answers for the given ids."""
        if self._answers_cache is None:
            return []
        
        examples = []
        for aid in ids[:max_examples]:
            row = self._answers_cache[self._answers_cache['original_index'] == aid]
            if not row.empty:
                examples.append(str(row.iloc[0]['answer']))
        
        return examples
    
    def get_cluster_details(self) -> List[dict]:
        """
        Detailed information on all clusters.
        
        Returns:
            list with the cluster details
        """
        if self.clustering_result is None:
            return []
        
        details = []
        for cluster in self.clustering_result.clusters:
            # all answers of the cluster
            answers = []
            if self._answers_cache is not None:
                for aid in cluster.answer_ids:
                    row = self._answers_cache[self._answers_cache['original_index'] == aid]
                    if not row.empty:
                        answers.append({
                            "id": aid,
                            "text": str(row.iloc[0]['answer'])
                        })
            
            details.append({
                "name": cluster.name,
                "description": cluster.description,
                "count": cluster.count,
                "examples": cluster.examples,
                "all_answers": answers
            })
        
        return details
    
    def export_cluster_mapping(self) -> Dict[int, str]:
        """
        Exports the mapping of answer ids to cluster names.
        
        Returns:
            dictionary {answer_id: cluster_name}
        """
        if self.clustering_result is None:
            return {}
        
        mapping = {}
        for cluster in self.clustering_result.clusters:
            for aid in cluster.answer_ids:
                mapping[aid] = cluster.name
        
        # mark as unclustered
        for aid in self.clustering_result.unclustered:
            mapping[aid] = "_unclustered"
        
        return mapping


def _part_of(description):
    """Description of a part of a split cluster: "Part of: …" in the language(s) of the original."""
    if isinstance(description, dict):
        return {lang: _text("clustering.part_of", lang, description=d) for lang, d in description.items()}
    from utils.prompts import source_language
    return _text("clustering.part_of", source_language(), description=description)
