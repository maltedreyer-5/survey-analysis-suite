# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Utility modules of the survey analysis tool: LLM integration, languages and
catalogs, segmentation, translations, charts, Word/JSON/CSV exports, the
database, item extraction, statistics and the survey structure adapters
(LimeSurvey, Qualtrics, SPSS).
"""

import ssl
import urllib.request
import logging

logger = logging.getLogger(__name__)

from .llm import LLMClient, get_llm_client, reset_llm_client
from .data_import import DataImporter, ImportResult
from .segmentation import SegmentationManager, SegmentInfo
from .translation import TranslationManager, t_report, get_translation_manager
from .charts import ChartGenerator
from .export_word import WordExporter, add_markdown_to_document
from .export_data import DataExporter
from .export_structured import StructuredReportExporter, export_structured_report
from .persistence import SurveyDatabase, get_database
from .item_extractor import ItemExtractor, RuleBasedExtractor, LLMExtractor, ExtractionResult
from .response_translator import ResponseTranslator, translate_responses
from .statistics import StatisticalAnalyzer
from .cluster_examples import ClusterExampleSelector, select_cluster_examples


def fetch_url(url: str, verify_ssl: bool = True, timeout: int = 30) -> str:
    """
    Loads the content of a URL.

    Args:
        url: the URL to fetch
        verify_ssl: verify the SSL certificate (False for self-signed certificates)
        timeout: timeout in seconds

    Returns:
        the content of the URL as a string

    Raises:
        Exception: on errors while fetching

    Example:
        # with SSL errors:
        content = fetch_url("https://example.com", verify_ssl=False)
    """
    try:
        if verify_ssl:
            context = ssl.create_default_context()
        else:
            # disable the SSL verification
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            logger.warning(f"⚠️ SSL verification disabled for: {url}")

        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )

        with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
            return response.read().decode('utf-8')

    except Exception as e:
        logger.error(f"Error fetching {url}: {e}")
        raise


# global label replacement dictionary (set by app.py)
_label_replacements = {}


def set_label_replacements(replacements: dict):
    """Sets the global label replacement dictionary."""
    global _label_replacements
    _label_replacements = replacements or {}


def get_label_replacements() -> dict:
    """Returns the current label replacement dictionary."""
    return _label_replacements


def apply_label_replacement(text: str) -> str:
    """
    Replaces long labels by shorter variants.

    Args:
        text: original text

    Returns:
        the shortened text, or the original if no replacement is defined
    """
    if not text or not _label_replacements:
        return text
    return _label_replacements.get(text, text)


def apply_label_replacements_to_dict(data: dict) -> dict:
    """
    Replaces the labels in a dictionary (keys).

    Args:
        data: dictionary with labels as keys

    Returns:
        dictionary with replaced keys
    """
    if not data or not _label_replacements:
        return data
    out: dict = {}
    for k, v in data.items():
        new_key = apply_label_replacement(k)
        if new_key in out and isinstance(v, (int, float)) and isinstance(out[new_key], (int, float)):
            logger.warning(f"Label replacement: '{k}' is merged with '{new_key}' (values added)")
            out[new_key] += v
        else:
            out[new_key] = v
    return out


def apply_label_replacements_to_list(items: list) -> list:
    """
    Replaces the labels in a list.

    Args:
        items: list of labels

    Returns:
        list with replaced labels
    """
    if not items or not _label_replacements:
        return items
    return [apply_label_replacement(item) for item in items]


__all__ = [
    'LLMClient',
    'get_llm_client',
    'reset_llm_client',
    'DataImporter',
    'ImportResult',
    'SegmentationManager',
    'SegmentInfo',
    'TranslationManager',
    'ChartGenerator',
    'WordExporter',
    'add_markdown_to_document',
    'DataExporter',
    'StructuredReportExporter',
    'export_structured_report',
    'SurveyDatabase',
    'get_database',
    'ItemExtractor',
    'RuleBasedExtractor',
    'LLMExtractor',
    'ExtractionResult',
    'set_label_replacements',
    'get_label_replacements',
    'apply_label_replacement',
    'apply_label_replacements_to_dict',
    'apply_label_replacements_to_list',
    'ResponseTranslator',
    'translate_responses',
    'StatisticalAnalyzer',
    'ClusterExampleSelector',
    'select_cluster_examples',
    'fetch_url',
]