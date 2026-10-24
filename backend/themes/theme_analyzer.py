# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Theme analysis module of the survey analysis tool.

Enables a cross-cutting thematic analysis of the survey results: extracts
keywords from questions and answers and assigns them to themes.

The result is exported as themes.yaml and is the basis of the thematic
navigation in the dashboard.
"""

import logging
import re
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field, asdict

import yaml
import pandas as pd

logger = logging.getLogger(__name__)


def _active_config_dir() -> Path:
    """The directory of the active survey (its themes.yaml); config/ outside the app."""
    try:
        from core import get_active_config_dir
        return Path(get_active_config_dir())
    except Exception:
        return Path(__file__).parent.parent / "config"


def _src(config: dict) -> str:
    from utils.languages import get_source_language
    return get_source_language(config)


def _text_in_source(value, config: dict, default: str = "") -> str:
    """A multilingual label in the source language of the survey."""
    from utils.languages import localized
    if isinstance(value, dict):
        src = _src(config)
        return str(localized(value, src, src) or default)
    return str(value or default)


def _initial_keywords(theme_data: dict, config: dict) -> list:
    """Question keywords of a stored theme; older entries keep them per language under 'keywords'."""
    value = theme_data.get("question_keywords")
    if value is None:
        value = (theme_data.get("keywords") or {}).get(_src(config), [])
    return list(value) if isinstance(value, list) else []


def _list_in_source(qconfig: dict, key: str, config: dict) -> list:
    """Options or item labels in the source language (dict per language or plain list)."""
    value = qconfig.get(key)
    if isinstance(value, dict):
        src = _src(config)
        return list(value.get(src) or next((v for v in value.values() if v), []) or [])
    return list(value or [])


@dataclass
class Theme:
    """A theme with keywords and assignments."""
    id: str
    label: dict = field(default_factory=lambda: {"de": "", "fr": "", "en": ""})
    description: dict = field(default_factory=lambda: {"de": "", "fr": "", "en": ""})
    icon: str = "📊"  # emoji icon for the dashboard
    question_keywords: list = field(default_factory=list)  # keywords in the source language
    keywords_translated: dict = field(default_factory=lambda: {"de": [], "fr": [], "en": []})  # multilingual
    answer_keywords: list = field(default_factory=list)
    questions: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Converts the theme into a dictionary for the export."""
        return {
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "question_keywords": self.question_keywords,
            "keywords_translated": self.keywords_translated,
            "answer_keywords": self.answer_keywords,
            "questions": self.questions,
            "stats": self.stats
        }


@dataclass
class ThemeAnalysisResult:
    """Result of the theme analysis."""
    themes: dict = field(default_factory=dict)
    question_theme_mapping: dict = field(default_factory=dict)
    keyword_index: dict = field(default_factory=dict)
    extraction_log: list = field(default_factory=list)


class ThemeAnalyzer:
    """
    Analyses survey data and extracts thematic assignments.
    
    Workflow:
    1. define themes (manually or from a default)
    2. extract keywords from the questions (LLM)
    3. extract keywords from the answers (LLM)
    4. refine the assignments manually
    5. export as themes.yaml
    """
    
    # No built-in themes: they belong to a survey and come from its themes.yaml
    # or are added in the interface (the former defaults belonged to one specific survey)
    DEFAULT_THEMES: dict = {}
    
    def __init__(
        self,
        config: dict,
        llm_client=None,
        output_dir: Optional[Path] = None,
        config_dir: Optional[Path] = None
    ):
        """
        Initialises the ThemeAnalyzer.
        
        Args:
            config: configuration from questions.yaml
            llm_client: LLM client for the keyword extraction
            output_dir: output directory of themes.yaml
            config_dir: directory with themes.yaml (default: config/)
        """
        self.config = config
        self.llm = llm_client
        self.output_dir = output_dir or Path(".")
        self.config_dir = Path(config_dir) if config_dir else _active_config_dir()
        
        # initialise the themes
        self.themes: dict[str, Theme] = {}
        self._init_themes()
        
        # analysis result
        self.result = ThemeAnalysisResult()
    
    def _load_themes_from_yaml(self) -> dict:
        """
        Loads themes from themes.yaml.
        
        Returns:
            dict with the themes, or an empty dict if the file does not exist
        """
        themes_file = self.config_dir / "themes.yaml"
        
        if not themes_file.exists():
            logger.info(f"themes.yaml not found at {themes_file}, using DEFAULT_THEMES")
            return {}
        
        try:
            import yaml
            with open(themes_file, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            themes = data.get('themes', {})
            logger.info(f"Themes loaded from {themes_file}: {len(themes)} themes")
            return themes
            
        except Exception as e:
            logger.warning(f"Error loading themes.yaml: {e}, using DEFAULT_THEMES")
            return {}
    
    def _init_themes(self):
        """Initialises the themes from themes.yaml, or DEFAULT_THEMES as a fallback."""
        # first try to load from YAML
        yaml_themes = self._load_themes_from_yaml()
        
        # default icons if not defined in YAML
        default_icons = {
            "ki": "🤖", "it_security": "🔒", "cloud": "☁️",
            "digital_sovereignty": "🛡️", "cooperation": "🤝",
            "budget": "💰", "services": "⚙️", "governance": "🏛️"
        }
        
        if yaml_themes:
            # use the themes from YAML
            for theme_id, theme_data in yaml_themes.items():
                self.themes[theme_id] = Theme(
                    id=theme_id,
                    label=theme_data.get("label", {"de": theme_id, "fr": theme_id, "en": theme_id}),
                    description=theme_data.get("description", {"de": "", "fr": "", "en": ""}),
                    icon=theme_data.get("icon", default_icons.get(theme_id, "📊")),
                    question_keywords=_initial_keywords(theme_data, self.config),
                    keywords_translated=theme_data.get("keywords", {"de": [], "fr": [], "en": []}),
                    answer_keywords=theme_data.get("answer_keywords", []).copy() if isinstance(theme_data.get("answer_keywords", []), list) else [],
                    questions=theme_data.get("questions", []).copy() if isinstance(theme_data.get("questions", []), list) else []
                )
        else:
            # fallback: use DEFAULT_THEMES
            self._init_default_themes()
    
    def _init_default_themes(self):
        """Initialises the predefined themes (fallback)."""
        # Standard-Icons
        default_icons = {
            "ki": "🤖", "it_security": "🔒", "cloud": "☁️",
            "digital_sovereignty": "🛡️", "cooperation": "🤝",
            "budget": "💰", "services": "⚙️", "governance": "🏛️"
        }
        
        for theme_id, theme_data in self.DEFAULT_THEMES.items():
            self.themes[theme_id] = Theme(
                id=theme_id,
                label=theme_data["label"],
                description=theme_data.get("description", {"de": "", "fr": "", "en": ""}),
                icon=theme_data.get("icon", default_icons.get(theme_id, "📊")),
                question_keywords=theme_data["question_keywords"].copy(),
                keywords_translated=theme_data.get("keywords_translated", {
                    "de": theme_data["question_keywords"].copy(),
                    "fr": [],
                    "en": []
                }),
                answer_keywords=theme_data.get("answer_keywords", []).copy(),
                questions=theme_data.get("questions", []).copy()  # explicit question assignment
            )
    
    def add_theme(
        self,
        theme_id: str,
        label_source: str,
        label_fr: str = "",
        label_en: str = "",
        keywords: list = None,
        labels: dict = None,
    ) -> Theme:
        """
        Adds a new theme.
        
        Args:
            theme_id: unique id of the theme
            label_source: label in the source language
            label_fr: French label
            label_en: English label
            keywords: initial keywords
            labels: labels per language ({lang: text}); if given, they replace
                label_source/label_fr/label_en (e.g. themes loaded from the database
                of a survey in another source language)
        
        Returns:
            the created Theme object
        """
        theme = Theme(
            id=theme_id,
            label=dict(labels) if labels else {
                "de": label_source,
                "fr": label_fr or label_source,
                "en": label_en or label_source
            },
            question_keywords=keywords or []
        )
        self.themes[theme_id] = theme
        logger.info(f"Theme added: {theme_id}")
        return theme
    
    def remove_theme(self, theme_id: str):
        """Removes a theme."""
        if theme_id in self.themes:
            del self.themes[theme_id]
            logger.info(f"Theme removed: {theme_id}")
    
    def extract_keywords_from_questions(
        self,
        use_llm: bool = True,
        progress_callback=None
    ) -> dict:
        """
        Extracts keywords from the question texts.
        
        Args:
            use_llm: use the LLM for an additional extraction
            progress_callback: callback for the progress display
        
        Returns:
            dictionary question id -> list of keywords
        """
        question_keywords = {}
        question_groups = self.config.get("question_groups", {})
        
        total = len(question_groups)
        
        for idx, (qid, qconfig) in enumerate(question_groups.items()):
            if progress_callback:
                progress_callback(idx / total, f"Analyzing: {qid}")
            
            # collect the question text(s)
            texts = []
            
            # Label
            texts.append(_text_in_source(qconfig.get("label", {}), self.config))
            
            # Spaltennamen
            columns = qconfig.get("columns", [])
            texts.extend(columns)
            
            # extract keywords (rule-based)
            keywords = self._extract_keywords_regex(texts)
            
            # LLM extraction (optional)
            if use_llm and self.llm:
                llm_keywords = self._extract_keywords_llm(texts)
                keywords.extend(llm_keywords)
            
            # Deduplizieren
            keywords = list(set(keywords))
            question_keywords[qid] = keywords
            
            # Log
            self.result.extraction_log.append({
                "type": "question",
                "id": qid,
                "keywords": keywords
            })
        
        if progress_callback:
            progress_callback(1.0, "Done")
        
        return question_keywords
    
    def _extract_keywords_regex(self, texts: list) -> list:
        """Extracts keywords from texts by rules."""
        keywords = []
        
        # collect all known keywords of the themes
        all_keywords = set()
        for theme in self.themes.values():
            all_keywords.update(theme.question_keywords)
            all_keywords.update(theme.answer_keywords)
        
        # search in the texts
        combined_text = " ".join(texts).lower()
        
        for kw in all_keywords:
            if kw.lower() in combined_text:
                keywords.append(kw)
        
        # extract item names from the columns (e.g. "[Artificial intelligence]")
        for text in texts:
            matches = re.findall(r'\[([^\]]+)\]', text)
            keywords.extend(matches)
        
        return keywords
    
    def _extract_keywords_llm(self, texts: list) -> list:
        """Extracts keywords with the LLM."""
        if not self.llm:
            return []
        
        try:
            from utils import prompts
            src = _src(self.config)
            context = prompts.context_text(self.config, src)
            response = self.llm.complete(
                prompts.prompt("themes.keywords", src, context=context, texts="\n".join(texts)),
                system_prompt=prompts.prompt("themes.keywords_system", src, context=context),
            )
            
            # extract the JSON from the response
            json_match = re.search(r'\[.*?\]', response, re.DOTALL)
            if json_match:
                keywords = json.loads(json_match.group())
                return keywords[:10]
            
        except Exception as e:
            logger.warning(f"LLM keyword extraction failed: {e}")
        
        return []
    
    def extract_keywords_from_answers(
        self,
        df: pd.DataFrame,
        analysis_results: dict,
        use_llm: bool = True,
        progress_callback=None
    ) -> dict:
        """
        Extracts keywords from answers (especially free-text clusters).
        
        Args:
            df: DataFrame with the survey data
            analysis_results: dictionary with the analysis results per question
            use_llm: use the LLM for the extraction
            progress_callback: callback for the progress
        
        Returns:
            dictionary question id -> list of keywords
        """
        answer_keywords = {}
        question_groups = self.config.get("question_groups", {})
        
        total = len(question_groups)
        
        for idx, (qid, qconfig) in enumerate(question_groups.items()):
            if progress_callback:
                progress_callback(idx / total, f"Analyzing answers: {qid}")
            
            handler_type = qconfig.get("handler", "")
            keywords = []
            
            # free text: cluster labels as keywords
            if handler_type == "freetext" and qid in analysis_results:
                result = analysis_results[qid]
                if result.clusters:
                    for cluster in result.clusters:
                        # cluster name as keyword
                        keywords.append(cluster.get("name", ""))
                        
                        # additional keywords from the cluster description (LLM)
                        if use_llm and self.llm and cluster.get("description"):
                            llm_kw = self._extract_keywords_llm([cluster["description"]])
                            keywords.extend(llm_kw)
            
            # other types: frequent answer categories
            elif qid in analysis_results:
                result = analysis_results[qid]
                if isinstance(result.data, dict):
                    # top categories as keywords
                    top_keys = list(result.data.keys())[:5]
                    keywords.extend(top_keys)
            
            # Deduplizieren
            keywords = list(set(k for k in keywords if k))
            answer_keywords[qid] = keywords
            
            self.result.extraction_log.append({
                "type": "answer",
                "id": qid,
                "keywords": keywords
            })
        
        if progress_callback:
            progress_callback(1.0, "Done")
        
        return answer_keywords
    
    def map_questions_to_themes(
        self,
        question_keywords: dict,
        answer_keywords: dict = None
    ) -> dict:
        """
        Assigns questions to themes by keywords.
        
        Args:
            question_keywords: keywords from the question texts
            answer_keywords: keywords from the answers
        
        Returns:
            mapping theme id -> list of question ids
        """
        answer_keywords = answer_keywords or {}
        theme_questions = {theme_id: [] for theme_id in self.themes}
        
        for qid, q_keywords in question_keywords.items():
            # all keywords of this question
            all_keywords = set(q_keywords)
            if qid in answer_keywords:
                all_keywords.update(answer_keywords[qid])
            
            # which themes match?
            for theme_id, theme in self.themes.items():
                theme_keywords = set(
                    theme.question_keywords + theme.answer_keywords
                )
                
                # a match if at least one keyword matches
                if all_keywords & theme_keywords:
                    theme_questions[theme_id].append(qid)
        
        # store the result, but only if no questions are predefined!
        for theme_id, questions in theme_questions.items():
            theme = self.themes[theme_id]
            if not theme.questions:
                theme.questions = list(set(questions))
            # otherwise: keep the predefined questions
        
        self.result.question_theme_mapping = theme_questions
        return theme_questions
    
    def add_keyword_to_theme(
        self,
        theme_id: str,
        keyword: str,
        keyword_type: str = "question"
    ):
        """
        Adds a keyword to a theme.
        
        Args:
            theme_id: id of the theme
            keyword: keyword
            keyword_type: "question" or "answer"
        """
        if theme_id not in self.themes:
            logger.warning(f"Theme not found: {theme_id}")
            return
        
        theme = self.themes[theme_id]
        
        if keyword_type == "question":
            if keyword not in theme.question_keywords:
                theme.question_keywords.append(keyword)
        else:
            if keyword not in theme.answer_keywords:
                theme.answer_keywords.append(keyword)
    
    def extract_keywords_for_theme(
        self,
        theme_id: str,
        use_llm: bool = True,
        check_data: bool = True
    ) -> dict:
        """
        Extracts keywords for a theme and checks where they occur in the data.
        
        Args:
            theme_id: id of the theme
            use_llm: use the LLM to generate keywords
            check_data: check the keywords against the actual data
        
        Returns:
            dictionary with:
            - keywords: list of the keywords
            - matches: {keyword: {questions: [...], count: n}}
            - assigned_questions: list of the assigned questions
        """
        if theme_id not in self.themes:
            logger.warning(f"Theme not found: {theme_id}")
            return {"keywords": [], "matches": {}, "assigned_questions": []}
        
        theme = self.themes[theme_id]
        theme_label = _text_in_source(theme.label, self.config, theme_id)
        
        # existing keywords as the starting point
        existing_keywords = list(theme.question_keywords)
        
        result = {
            "keywords": existing_keywords.copy(),
            "matches": {},
            "assigned_questions": []
        }
        
        question_groups = self.config.get("question_groups", {})
        
        # step 1: generate keywords with the LLM (if enabled)
        if use_llm and self.llm:
            try:
                # collect all question texts for context
                question_texts = []
                for qid, qconfig in question_groups.items():
                    text = _text_in_source(qconfig.get("label", {}), self.config)
                    if text:
                        question_texts.append(f"{qid}: {text}")
                
                # LLM prompt for theme-specific keywords (catalog of the source language)
                from utils import prompts
                src = _src(self.config)
                context = prompts.context_text(self.config, src)
                response = self.llm.complete(
                    prompts.prompt("themes.theme_keywords", src, theme=theme_label, context=context,
                                   questions="\n".join(question_texts[:40])),
                    system_prompt=prompts.prompt("themes.theme_keywords_system", src, context=context),
                )
                
                # extract the JSON from the response
                json_match = re.search(r'\[.*?\]', response, re.DOTALL)
                if json_match:
                    new_keywords = json.loads(json_match.group())
                    for kw in new_keywords:
                        if kw and kw not in result["keywords"]:
                            result["keywords"].append(kw)
                    
                    logger.info(f"Theme '{theme_id}': {len(new_keywords)} keywords generated by the LLM")
                
            except Exception as e:
                logger.warning(f"LLM keyword generation failed: {e}")
        
        # step 2: check the keywords against the data (if enabled)
        if check_data and result["keywords"]:
            from core import state
            
            for keyword in result["keywords"]:
                keyword_lower = keyword.lower()
                matches = {
                    "questions": [],
                    "question_count": 0,
                    "answer_count": 0
                }
                
                # search in the questions
                for qid, qconfig in question_groups.items():
                    # check the label
                    label_text = _text_in_source(qconfig.get("label", {}), self.config)
                    
                    # check the columns
                    columns = qconfig.get("columns", [])
                    columns_text = " ".join(columns)
                    
                    # check the options (for choice questions)
                    options_text = " ".join(str(o) for o in _list_in_source(qconfig, "options", self.config))
                    
                    # check the item labels (for multi_choice_binary)
                    item_text = " ".join(str(o) for o in _list_in_source(qconfig, "item_labels", self.config))
                    
                    combined_text = f"{label_text} {columns_text} {options_text} {item_text}".lower()
                    
                    if keyword_lower in combined_text:
                        matches["questions"].append(qid)
                        matches["question_count"] += 1
                
                # search in the answers (if a DataFrame is available)
                if state.df is not None:
                    for qid, qconfig in question_groups.items():
                        columns = qconfig.get("columns", [])
                        for col in columns:
                            if col in state.df.columns:
                                # text search in the column
                                col_data = state.df[col].dropna().astype(str)
                                count = col_data.str.lower().str.contains(keyword_lower, regex=False).sum()
                                if count > 0:
                                    matches["answer_count"] += count
                                    if qid not in matches["questions"]:
                                        matches["questions"].append(qid)
                
                if matches["question_count"] > 0 or matches["answer_count"] > 0:
                    result["matches"][keyword] = matches
            
            # assign questions with at least one keyword match to the theme
            all_matched_questions = set()
            for keyword, match_data in result["matches"].items():
                all_matched_questions.update(match_data["questions"])
            
            result["assigned_questions"] = list(all_matched_questions)
            
            logger.info(f"Theme '{theme_id}': {len(result['matches'])} keywords found, "
                       f"{len(result['assigned_questions'])} questions assigned")
        
        # store the keywords in the theme
        theme.question_keywords = result["keywords"]
        
        # IMPORTANT: explicitly defined questions take precedence!
        # keyword matching only if no questions are predefined
        if not theme.questions:
            theme.questions = result["assigned_questions"]
            logger.info(f"Theme '{theme_id}': using {len(result['assigned_questions'])} keyword-matched questions")
        else:
            # keep the questions already defined, optionally add
            existing = set(theme.questions)
            new_matches = [q for q in result["assigned_questions"] if q not in existing]
            if new_matches:
                logger.info(f"Theme '{theme_id}': keeping {len(theme.questions)} predefined questions, "
                           f"ignoring {len(new_matches)} keyword matches")
            result["assigned_questions"] = theme.questions  # for the return value
        
        return result
    
    def deep_analyze_theme(
        self,
        theme_id: str,
        progress_callback=None
    ) -> dict:
        """
        Deep analysis of a theme, the LLM checks every cluster.
        
        NOTE: makes many LLM calls (1 per question with clusters)!
        
        Args:
            theme_id: id of the theme
            progress_callback: optional callback(progress, message)
        
        Returns:
            dictionary with detailed results
        """
        if theme_id not in self.themes:
            return {"error": f"Theme '{theme_id}' not found"}
        
        if not self.llm:
            return {"error": "LLM not available"}
        
        from core import state
        
        theme = self.themes[theme_id]
        theme_label = _text_in_source(theme.label, self.config, theme_id)
        
        result = {
            "theme_id": theme_id,
            "theme_label": theme_label,
            "analyzed_questions": [],
            "matched_questions": [],
            "cluster_matches": [],
            "llm_calls": 0
        }
        
        question_groups = self.config.get("question_groups", {})
        
        # collect all questions with free-text clusters
        freetext_questions = []
        for qid, qconfig in question_groups.items():
            if qconfig.get("handler") == "freetext":
                freetext_questions.append((qid, qconfig))
        
        # collect all choice questions
        choice_questions = []
        for qid, qconfig in question_groups.items():
            handler = qconfig.get("handler", "")
            if handler in ["single_choice", "multi_choice", "multi_choice_binary"]:
                choice_questions.append((qid, qconfig))
        
        total_items = len(freetext_questions) + len(choice_questions)
        processed = 0
        
        # 1. analyse the free-text clusters
        for qid, qconfig in freetext_questions:
            if progress_callback:
                progress_callback(processed / total_items, f"Analyzing clusters: {qid}")
            
            # get the clusters from the database or the cache
            clusters = self._get_clusters_for_question(qid)
            
            if clusters:
                # ask the LLM: which clusters fit the theme?
                cluster_names = [c.get("name", "") for c in clusters]
                
                try:
                    from utils import prompts
                    src = _src(self.config)
                    response = self.llm.complete(
                        prompts.prompt("themes.cluster_match", src, theme=theme_label,
                                       question=_text_in_source(qconfig.get('label', {}), self.config, qid),
                                       items="\n".join(f"- {name}" for name in cluster_names)),
                        system_prompt=prompts.prompt("themes.cluster_match_system", src),
                    )
                    result["llm_calls"] += 1
                    
                    json_match = re.search(r'\[.*?\]', response, re.DOTALL)
                    if json_match:
                        matching_clusters = json.loads(json_match.group())
                        
                        if matching_clusters:
                            result["matched_questions"].append(qid)
                            for cluster_name in matching_clusters:
                                result["cluster_matches"].append({
                                    "question_id": qid,
                                    "cluster": cluster_name,
                                    "type": "freetext"
                                })
                
                except Exception as e:
                    logger.warning(f"LLM cluster analysis failed for {qid}: {e}")
            
            result["analyzed_questions"].append(qid)
            processed += 1
        
        # 2. analyse the choice options
        for qid, qconfig in choice_questions:
            if progress_callback:
                progress_callback(processed / total_items, f"Analyzing options: {qid}")
            
            # collect the options
            options = _list_in_source(qconfig, "options", self.config)
            item_labels = _list_in_source(qconfig, "item_labels", self.config)
            all_options = options + item_labels
            
            if all_options:
                try:
                    from utils import prompts
                    src = _src(self.config)
                    response = self.llm.complete(
                        prompts.prompt("themes.option_match", src, theme=theme_label,
                                       question=_text_in_source(qconfig.get('label', {}), self.config, qid),
                                       items="\n".join(f"- {opt}" for opt in all_options)),
                        system_prompt=prompts.prompt("themes.option_match_system", src),
                    )
                    result["llm_calls"] += 1
                    
                    json_match = re.search(r'\[.*?\]', response, re.DOTALL)
                    if json_match:
                        matching_options = json.loads(json_match.group())
                        
                        if matching_options:
                            if qid not in result["matched_questions"]:
                                result["matched_questions"].append(qid)
                            for opt in matching_options:
                                result["cluster_matches"].append({
                                    "question_id": qid,
                                    "cluster": opt,
                                    "type": "option"
                                })
                
                except Exception as e:
                    logger.warning(f"LLM option analysis failed for {qid}: {e}")
            
            result["analyzed_questions"].append(qid)
            processed += 1
        
        if progress_callback:
            progress_callback(1.0, "Done")
        
        # update the theme, but only if no questions are predefined!
        if not theme.questions:
            theme.questions = result["matched_questions"]
            logger.info(f"Deep analysis '{theme_id}': {result['llm_calls']} LLM calls, "
                       f"{len(result['matched_questions'])} questions assigned")
        else:
            logger.info(f"Deep analysis '{theme_id}': keeping {len(theme.questions)} predefined questions, "
                       f"the LLM found {len(result['matched_questions'])} matches")
            result["matched_questions"] = theme.questions  # for the return value
        
        return result
    
    def _get_clusters_for_question(self, qid: str) -> list:
        """Gets the clusters of a question from the database or the cache."""
        from core import state
        
        if not state.db or not state.import_id:
            return []
        
        try:
            with state.db._connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT fc.cluster_name, fc.description_source
                    FROM freetext_clusters fc
                    JOIN analysis_results ar ON fc.analysis_id = ar.id
                    WHERE ar.question_id = ? AND ar.import_id = ? AND ar.segment_type = 'all'
                """, (qid, state.import_id))
                
                rows = cursor.fetchall()
                return [{"name": row[0], "description": row[1]} for row in rows]
        except:
            return []
    
    def remove_keyword_from_theme(
        self,
        theme_id: str,
        keyword: str,
        keyword_type: str = "question"
    ):
        """Removes a keyword from a theme."""
        if theme_id not in self.themes:
            return
        
        theme = self.themes[theme_id]
        
        if keyword_type == "question":
            if keyword in theme.question_keywords:
                theme.question_keywords.remove(keyword)
        else:
            if keyword in theme.answer_keywords:
                theme.answer_keywords.remove(keyword)
    
    def calculate_theme_stats(
        self,
        df: pd.DataFrame,
        analysis_results: dict
    ) -> dict:
        """
        Computes statistics per theme.
        
        Args:
            df: DataFrame with the survey data
            analysis_results: analysis results
        
        Returns:
            dictionary with statistics per theme
        """
        stats = {}
        
        for theme_id, theme in self.themes.items():
            theme_stats = {
                "question_count": len(theme.questions),
                "mentions_in_freetext": 0,
                "related_keywords": []
            }
            
            # count the free-text mentions
            for qid in theme.questions:
                if qid in analysis_results:
                    result = analysis_results[qid]
                    if result.clusters:
                        for cluster in result.clusters:
                            # does the cluster name contain a theme keyword?
                            cluster_name = cluster.get("name", "").lower()
                            for kw in theme.question_keywords + theme.answer_keywords:
                                if kw.lower() in cluster_name:
                                    theme_stats["mentions_in_freetext"] += cluster.get("count", 0)
                                    break
            
            # collect the keywords
            theme_stats["related_keywords"] = (
                theme.question_keywords + theme.answer_keywords
            )[:20]
            
            theme.stats = theme_stats
            stats[theme_id] = theme_stats
        
        return stats
    
    def export_themes(self, filename: str = "themes.yaml") -> Path:
        """
        Exports the theme analysis as YAML.
        
        Args:
            filename: name of the output file
        
        Returns:
            path of the exported file
        """
        output = {
            "themes": {},
            "metadata": {
                "total_themes": len(self.themes),
                "extraction_log_entries": len(self.result.extraction_log)
            }
        }
        
        for theme_id, theme in self.themes.items():
            output["themes"][theme_id] = theme.to_dict()
        
        filepath = self.output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            yaml.dump(output, f, allow_unicode=True, default_flow_style=False)
        
        logger.info(f"Themes exported to: {filepath}")
        return filepath
    
    def load_themes(self, filepath: Path) -> dict:
        """
        Loads themes from a YAML file.
        
        Args:
            filepath: path of themes.yaml
        
        Returns:
            the loaded themes
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        
        themes_data = data.get("themes", {})
        
        for theme_id, theme_data in themes_data.items():
            self.themes[theme_id] = Theme(
                id=theme_id,
                label=theme_data.get("label", {}),
                question_keywords=theme_data.get("question_keywords", []),
                answer_keywords=theme_data.get("answer_keywords", []),
                questions=theme_data.get("questions", []),
                stats=theme_data.get("stats", {})
            )
        
        logger.info(f"Themes loaded from: {filepath}")
        return self.themes
    
    def get_theme_summary(self) -> str:
        """Returns a summary of the theme analysis."""
        lines = ["# Theme overview", ""]
        
        for theme_id, theme in sorted(self.themes.items()):
            lines.append(f"## {_text_in_source(theme.label, self.config, theme_id)}")
            lines.append(f"- ID: `{theme_id}`")
            lines.append(f"- Question keywords: {len(theme.question_keywords)}")
            lines.append(f"- Answer keywords: {len(theme.answer_keywords)}")
            lines.append(f"- Assigned questions: {len(theme.questions)}")
            
            if theme.questions:
                lines.append(f"- Questions: {', '.join(theme.questions[:5])}" + 
                           ("..." if len(theme.questions) > 5 else ""))
            
            lines.append("")
        
        return "\n".join(lines)
