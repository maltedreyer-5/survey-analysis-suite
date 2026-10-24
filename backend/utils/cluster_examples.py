# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Selection of the most telling cluster examples: the LLM picks representative
answers.
"""

import logging
import json
import re
from typing import List, Dict, Optional

from .llm import LLMClient, get_llm_client

logger = logging.getLogger(__name__)


class ClusterExampleSelector:
    """Selects the most telling examples from the answers of a cluster."""
    
    def __init__(self, llm: LLMClient = None):
        """
        Initialises the ClusterExampleSelector.
        
        Args:
            llm: LLM client (or None to create one automatically)
        """
        self.llm = llm or get_llm_client()
    
    def select_best_examples(
        self,
        cluster_name: str,
        cluster_description: str,
        all_answers: List[str],
        n_examples: int = 5,
        lang: str = "de"
    ) -> List[str]:
        """
        Selects the best examples of a cluster.
        
        Args:
            cluster_name: name of the cluster
            cluster_description: description of the cluster
            all_answers: all answers in the cluster
            n_examples: number of examples wanted (5-10)
            lang: language
        
        Returns:
            list of the best examples
        """
        if not all_answers:
            return []
        
        # few answers: return all
        if len(all_answers) <= n_examples:
            return all_answers
        
        # LLM selection
        try:
            return self._select_with_llm(
                cluster_name, cluster_description, all_answers, n_examples, lang
            )
        except Exception as e:
            logger.warning(f"LLM selection failed, using the fallback: {e}")
            return self._select_fallback(all_answers, n_examples)
    
    def _select_with_llm(
        self,
        cluster_name: str,
        cluster_description: str,
        all_answers: List[str],
        n_examples: int,
        lang: str
    ) -> List[str]:
        """LLM selection of the best examples."""
        
        # build the prompt
        answers_text = "\n".join(
            f"{i+1}. {answer}" 
            for i, answer in enumerate(all_answers[:50])  # at most 50 for the context
        )
        
        # The selection does not depend on the report language: the prompt
        # comes from the catalog of the survey's source language, in which
        # the answers are written.
        from utils import prompts
        from utils.languages import localized
        src = prompts.source_language()
        name = str(localized(cluster_name, src, src) or "") if isinstance(cluster_name, dict) else str(cluster_name or "")
        description = (str(localized(cluster_description, src, src) or "")
                       if isinstance(cluster_description, dict) else str(cluster_description or ""))
        prompt = prompts.prompt("cluster_examples.user", src, context=prompts.context_text(None, src),
                                name=name,
                                description=description or prompts.get("cluster_examples.no_description", src),
                                answers=answers_text, count=n_examples)
        response = self.llm.complete(prompt, system_prompt=prompts.prompt("cluster_examples.system", src))
        
        # extract the JSON from the response
        json_match = re.search(r'\[[\d,\s]+\]', response)
        if json_match:
            indices = json.loads(json_match.group())
            # 1-based indices to 0-based; only the numbers shown to the LLM count
            selected = []
            for idx in indices:
                if 1 <= idx <= min(len(all_answers), 50):
                    selected.append(all_answers[idx - 1])
            
            if selected:
                return selected[:n_examples]
        
        # fallback if the parsing fails
        logger.warning("Could not parse the LLM response, using the fallback")
        return self._select_fallback(all_answers, n_examples)
    
    def _select_fallback(
        self,
        all_answers: List[str],
        n_examples: int
    ) -> List[str]:
        """
        Fallback selection by text length and diversity: answers of medium
        length that contain different words.
        """
        if len(all_answers) <= n_examples:
            return all_answers
        
        # sort by length (prefer medium length)
        lengths = [(i, len(a)) for i, a in enumerate(all_answers)]
        median_length = sorted([l for _, l in lengths])[len(lengths) // 2]
        
        # score by the distance from the medium length
        scored = []
        for i, answer in enumerate(all_answers):
            length = len(answer)
            length_score = 1 / (1 + abs(length - median_length) / median_length)
            
            # bonus for answers with more distinct words
            unique_words = len(set(answer.lower().split()))
            word_score = min(unique_words / 10, 1.0)
            
            total_score = length_score * 0.6 + word_score * 0.4
            scored.append((i, total_score, answer))
        
        # top n by score
        scored.sort(key=lambda x: x[1], reverse=True)
        return [answer for _, _, answer in scored[:n_examples]]
    
    def select_examples_for_clusters(
        self,
        cluster_details: List[Dict],
        n_examples: int = 5,
        lang: str = "de"
    ) -> List[Dict]:
        """
        Selects examples for all clusters.
        
        Args:
            cluster_details: list of cluster dicts with 'name', 'description', 'all_answers'
            n_examples: number of examples per cluster
            lang: language
        
        Returns:
            cluster details with the selected 'selected_examples'
        """
        for cluster in cluster_details:
            all_answers = cluster.get('all_answers', [])
            # extract only the texts
            if all_answers and isinstance(all_answers[0], dict):
                all_texts = [a.get('text', '') for a in all_answers if a.get('text')]
            else:
                all_texts = [str(a) for a in all_answers if a]
            
            selected = self.select_best_examples(
                cluster_name=cluster.get('name', ''),
                cluster_description=cluster.get('description', ''),
                all_answers=all_texts,
                n_examples=n_examples,
                lang=lang
            )
            
            cluster['selected_examples'] = selected
        
        return cluster_details


def select_cluster_examples(
    cluster_details: List[Dict],
    n_examples: int = 5,
    lang: str = "de",
    llm: LLMClient = None
) -> List[Dict]:
    """
    Main function: selects telling examples for all clusters.
    
    Args:
        cluster_details: list of cluster dictionaries
        n_examples: number of examples per cluster (5-10)
        lang: language
        llm: optional LLM client
    
    Returns:
        cluster details with a 'selected_examples' field
    """
    selector = ClusterExampleSelector(llm=llm)
    return selector.select_examples_for_clusters(cluster_details, n_examples, lang)
