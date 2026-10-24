# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Item extractor for free-text answers.

Extracts single, atomic answer items from combined free-text answers, with a
hybrid strategy: rules first, then the LLM for complex cases.
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """Result of an item extraction."""
    items: List[str]
    method: str
    confidence: float
    original_text: str = ""
    reasoning: Optional[str] = None
    
    @property
    def item_count(self) -> int:
        return len(self.items)
    
    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0


@dataclass
class ExtractionStats:
    """Statistics of a batch extraction."""
    total_responses: int = 0
    processed_responses: int = 0
    total_items: int = 0
    methods: Dict[str, int] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    avg_items_per_response: float = 0.0
    
    def to_dict(self) -> Dict:
        return {
            'total_responses': self.total_responses,
            'processed_responses': self.processed_responses,
            'total_items': self.total_items,
            'methods': self.methods,
            'errors': self.errors[:10],  # at most 10 errors
            'error_count': len(self.errors),
            'avg_items_per_response': round(self.avg_items_per_response, 2)
        }


class RuleBasedExtractor:
    """
    Rule-based item extraction.
    
    Detects structured answers by:
    - line breaks
    - semicolons
    - numbered lists
    - bullet points
    """
    
    # patterns of list prefixes
    LIST_PREFIX_PATTERNS = [
        r'^\s*(\d+[\.\)]\s*)',           # 1. or 1)
        r'^\s*([a-zA-Z][\.\)]\s*)',      # a. or a)
        r'^\s*([-•●○◦▪▸►→]\s*)',         # Bullet Points
        r'^\s*(\*\s+)',                   # Markdown-Bullet
    ]
    
    # Kompilierte Patterns
    _list_prefix_regex = re.compile('|'.join(f'({p})' for p in LIST_PREFIX_PATTERNS))
    
    def extract(self, text: str) -> Optional[ExtractionResult]:
        """
        Tries the rule-based extraction.
        
        Args:
            text: text to analyse
        
        Returns:
            ExtractionResult if successful, otherwise None
        """
        if not text:
            return ExtractionResult(
                items=[], 
                method='empty', 
                confidence=1.0,
                original_text=""
            )
        
        text = text.strip()
        original = text
        
        # 1. very short text without separators -> single item
        if len(text) < 50 and '\n' not in text and ';' not in text:
            # no hidden list indicators?
            if not re.search(r'\d+[\.\)]', text):
                return ExtractionResult(
                    items=[text],
                    method='single',
                    confidence=1.0,
                    original_text=original
                )
        
        # 2. line-based extraction
        if '\n' in text:
            result = self._extract_by_newline(text, original)
            if result:
                return result
        
        # 3. dash separation (e.g. "- Item1 - Item2" or "Item1 - Item2 - Item3")
        if ' - ' in text or text.startswith('- '):
            result = self._extract_by_dash(text, original)
            if result:
                return result
        
        # 4. semicolon separation
        if ';' in text:
            result = self._extract_by_semicolon(text, original)
            if result:
                return result
        
        # 4. inline numbered list (e.g. "1. A 2. B 3. C")
        result = self._extract_inline_numbered(text, original)
        if result:
            return result
        
        # 5. comma-separated lists with "and"/"or" at the end
        result = self._extract_comma_list(text, original)
        if result:
            return result
        
        # 6. fallback: the text has no clear structure -> treat it as a single item
        # (only if the text is substantial)
        if len(text) >= 3:
            return ExtractionResult(
                items=[text],
                method='single_fallback',
                confidence=0.5,
                original_text=original
            )
        
        # no clear structure detected and the text is too short
        return None
    
    def _extract_by_newline(
        self, 
        text: str, 
        original: str
    ) -> Optional[ExtractionResult]:
        """Extracts items by line breaks."""
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        if len(lines) <= 1:
            return None
        
        # are the lines independent items?
        if not self._are_distinct_items(lines):
            return None
        
        # remove list prefixes
        cleaned = [self._clean_list_prefix(l) for l in lines]
        cleaned = [l for l in cleaned if l]  # remove empty ones
        
        if len(cleaned) < 2:
            return None
        
        return ExtractionResult(
            items=cleaned,
            method='newline',
            confidence=0.95,
            original_text=original
        )
    
    def _extract_by_semicolon(
        self, 
        text: str, 
        original: str
    ) -> Optional[ExtractionResult]:
        """Extracts items by semicolons."""
        parts = [p.strip() for p in text.split(';') if p.strip()]
        
        if len(parts) <= 1:
            return None
        
        # all parts should be substantial
        if not all(len(p) >= 2 for p in parts):
            return None
        
        # not too many very short parts (could be running text)
        short_parts = sum(1 for p in parts if len(p) < 10)
        if short_parts > len(parts) * 0.7:
            # probably a real list
            pass
        
        return ExtractionResult(
            items=parts,
            method='semicolon',
            confidence=0.90,
            original_text=original
        )
    
    def _extract_by_dash(
        self, 
        text: str, 
        original: str
    ) -> Optional[ExtractionResult]:
        """
        Extracts items by dash separators.
        
        Detects patterns such as:
        - "- Item1 - Item2 - Item3"
        - "Item1 - Item2 - Item3"
        - "- Item1\n- Item2" (caught by the newline handler)
        """
        # pattern 1: the text starts with "- " -> split at " - "
        if text.startswith('- '):
            # "- A - B - C" -> ["A", "B", "C"]
            parts = text[2:].split(' - ')  # remove the first "- "
        else:
            # "A - B - C" -> ["A", "B", "C"]
            parts = text.split(' - ')
        
        parts = [p.strip() for p in parts if p.strip()]
        
        if len(parts) <= 1:
            return None
        
        # all parts should be substantial (at least 3 characters)
        if not all(len(p) >= 3 for p in parts):
            return None
        
        # is this really a list and not a sentence with dashes?
        # heuristic: parts of very different length are probably no list
        lengths = [len(p) for p in parts]
        avg_len = sum(lengths) / len(lengths)
        
        # a part more than 3x as long as the average: be sceptical
        if any(l > avg_len * 3 for l in lengths) and avg_len > 50:
            return None
        
        return ExtractionResult(
            items=parts,
            method='dash',
            confidence=0.85,
            original_text=original
        )
    
    def _extract_inline_numbered(
        self, 
        text: str, 
        original: str
    ) -> Optional[ExtractionResult]:
        """Extracts inline numbered lists such as '1. A 2. B 3. C'."""
        # pattern: number followed by text up to the next number
        pattern = r'(?:^|\s)(\d+)[\.\)]\s*([^0-9]+?)(?=\s*\d+[\.\)]|$)'
        matches = re.findall(pattern, text)
        
        if len(matches) < 2:
            return None
        
        # sort by number and extract the text
        items = []
        for num, content in sorted(matches, key=lambda x: int(x[0])):
            cleaned = content.strip().rstrip(',;.')
            if cleaned:
                items.append(cleaned)
        
        if len(items) < 2:
            return None
        
        return ExtractionResult(
            items=items,
            method='numbered_inline',
            confidence=0.85,
            original_text=original
        )
    
    def _extract_comma_list(
        self, 
        text: str, 
        original: str
    ) -> Optional[ExtractionResult]:
        """
        Extracts comma-separated lists with a conjunction,
        e.g. "Moodle, Ilias and OLAT"
        """
        # pattern: "A, B, C and/or D"
        pattern = r'^(.+?)(?:,\s*|\s+)(und|oder|sowie|and|or)\s+([^,]+)$'
        match = re.match(pattern, text, re.IGNORECASE)
        
        if not match:
            return None
        
        before_conj = match.group(1)
        conjunction = match.group(2)
        after_conj = match.group(3)
        
        # split the parts before the conjunction
        parts = [p.strip() for p in before_conj.split(',') if p.strip()]
        parts.append(after_conj.strip())
        
        if len(parts) < 2:
            return None
        
        # all parts should be similarly short (no sentences)
        if any(len(p) > 50 for p in parts):
            return None
        
        return ExtractionResult(
            items=parts,
            method='comma_conjunction',
            confidence=0.80,
            original_text=original
        )
    
    def _are_distinct_items(self, lines: List[str]) -> bool:
        """
        Checks whether lines are independent items.
        
        Heuristics:
        - no line ends with a comma (except the last)
        - lines start alike (capital letter, bullet)
        - lines are not too different in length
        """
        if len(lines) < 2:
            return False
        
        # check for bullet characters
        has_prefix = [bool(self._list_prefix_regex.match(l)) for l in lines]
        if all(has_prefix):
            return True
        
        # check for a consistent structure:
        # lines should start with a capital letter or a bullet
        starts_uppercase = sum(1 for l in lines if l and l[0].isupper())
        if starts_uppercase >= len(lines) * 0.8:
            # most lines start with a capital letter
            
            # no line should end with a comma (that would continue a sentence)
            ends_comma = sum(1 for l in lines[:-1] if l.rstrip().endswith(','))
            if ends_comma == 0:
                return True
        
        # short lines are rather items
        avg_len = sum(len(l) for l in lines) / len(lines)
        if avg_len < 40:
            return True
        
        return False
    
    def _clean_list_prefix(self, text: str) -> str:
        """Removes list prefixes at the start."""
        return self._list_prefix_regex.sub('', text).strip()


class LLMExtractor:
    """
    LLM item extraction for complex cases.
    
    Used when:
    - the rule-based extraction fails
    - the text is running text with several answers
    - the confidence of the rule-based extraction is too low
    """
    
    
    # Prompts: prompts/<source language>.yaml, section item_extraction
    # (syntactic = conservative splitting, thematic = split by topic but keep
    # all details, validation = check that details survived, retry = second
    # attempt with the validation feedback).

    def _prompt(self, key: str, **fields) -> str:
        from utils import prompts
        return prompts.prompt(f"item_extraction.{key}", self.source_language, **fields)

    def __init__(self, llm_client, question_context: str = "", thematic_mode: bool = False):
        """
        Initialises the LLM extractor.
        
        Args:
            llm_client: LLM client instance
            question_context: context of the question (helps the interpretation)
            thematic_mode: if True, splits by different topics (more items, with details)
        """
        from utils import prompts
        self.llm = llm_client
        self.question_context = question_context
        self.thematic_mode = thematic_mode
        self.source_language = prompts.source_language()
    
    def extract_sync(self, text: str, thematic: bool = None) -> ExtractionResult:
        """
        Synchronous LLM extraction (recommended).
        
        Args:
            text: text to analyse
            thematic: overrides self.thematic_mode if set.
                     True = thematic splitting (more items, details kept)
                     False = syntactic splitting (conservative)
        
        Returns:
            ExtractionResult
        """
        use_thematic = thematic if thematic is not None else self.thematic_mode
        
        if not text or not text.strip():
            return ExtractionResult(
                items=[],
                method='empty',
                confidence=1.0,
                original_text=""
            )
        
        if self.llm is None:
            logger.warning("LLM client is None, cannot extract")
            return ExtractionResult(
                items=[text.strip()],
                method='llm_unavailable',
                confidence=0.3,
                original_text=text,
                reasoning="LLM client not available"
            )
        
        text = text.strip()
        user_prompt = self._build_prompt(text)
        
        # choose the prompt by mode
        system_prompt = self._prompt('thematic' if use_thematic else 'syntactic')
        method_suffix = '_thematic' if use_thematic else ''
        
        try:
            logger.info(f"LLM extraction started {'(thematic)' if use_thematic else '(syntactic)'}: {text[:80]}...")
            
            # call synchronously
            response = self.llm.complete(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=2000  # increased from 1000 for longer lists
            )
            
            logger.info(f"LLM answer received ({len(response)} characters)")
            logger.debug(f"LLM answer: {response[:200]}...")
            
            result = self._parse_response(response, text)
            result.method = f'llm{method_suffix}'
            logger.info(f"LLM extrahierte {len(result.items)} Items: {result.items[:3]}{'...' if len(result.items) > 3 else ''}")
            
            return result
            
        except Exception as e:
            logger.error(f"LLM extraction failed: {e}", exc_info=True)
            # fallback: the original text as a single item
            return ExtractionResult(
                items=[text],
                method='llm_error',
                confidence=0.5,
                original_text=text,
                reasoning=f"LLM error: {str(e)}"
            )
    
    async def extract(self, text: str, thematic: bool = None) -> ExtractionResult:
        """
        Async LLM extraction (for compatibility); calls extract_sync.
        """
        return self.extract_sync(text, thematic=thematic)
    
    def _validate_extraction(self, original: str, items: List[str]) -> Tuple[bool, str]:
        """
        Validates whether the extraction kept the meaning.
        
        Returns:
            tuple (is_valid, reason)
        """
        if self.llm is None:
            return True, "LLM not available for validation"
        
        # short texts need no validation
        if len(original) < 30:
            return True, "Text too short for validation"
        
        # single item that is almost identical -> OK
        if len(items) == 1 and len(items[0]) >= len(original) * 0.8:
            return True, "Item equals the original"
        
        prompt = self._prompt('validation_user', original=original,
                              items=json.dumps(items, ensure_ascii=False))
        
        try:
            response = self.llm.complete(
                prompt=prompt,
                system_prompt=self._prompt('validation'),
                temperature=0.3,
                max_tokens=500  # increased from 200
            )
            
            # parse the JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                is_valid = data.get('valid', True)
                reason = data.get('reason', 'No reason given')
                
                logger.info(f"Validation: valid={is_valid}, reason={reason[:50]}...")
                return is_valid, reason
            
            return True, "Parsing failed, assuming OK"
            
        except Exception as e:
            logger.warning(f"Validation failed: {e}")
            return True, f"Validation error: {e}"
    
    def _retry_extraction(self, original: str, previous_items: List[str], validation_reason: str) -> ExtractionResult:
        """
        Repeats the extraction with feedback on the error.
        """
        retry_system = self._prompt(
            'retry',
            previous_items=json.dumps(previous_items, ensure_ascii=False),
            validation_reason=validation_reason,
        )
        
        user_prompt = self._build_prompt(original)
        
        try:
            response = self.llm.complete(
                prompt=user_prompt,
                system_prompt=retry_system,
                temperature=0.3,
                max_tokens=2000  # increased from 1000
            )
            
            result = self._parse_response(response, original)
            result.method = 'llm_retry'
            logger.info(f"Retry extrahierte {len(result.items)} Items: {result.items}")
            return result
            
        except Exception as e:
            logger.error(f"Retry failed: {e}")
            return ExtractionResult(
                items=[original],
                method='llm_retry_error',
                confidence=0.5,
                original_text=original,
                reasoning=f"Retry error: {e}"
            )
    
    def extract_with_validation(self, text: str, thematic: bool = True) -> ExtractionResult:
        """
        Extracts items with a quality check and a retry if needed.
        
        Steps:
        1. first extraction
        2. LLM validation: are the details kept?
        3. on problems: retry with feedback
        
        Args:
            text: text to analyse
            thematic: use thematic splitting
        
        Returns:
            ExtractionResult
        """
        if not text or not text.strip():
            return ExtractionResult(
                items=[],
                method='empty',
                confidence=1.0,
                original_text=""
            )
        
        text = text.strip()
        
        # 1. first extraction
        result = self.extract_sync(text, thematic=thematic)
        
        if not result.items:
            return result
        
        # 2. validation
        is_valid, reason = self._validate_extraction(text, result.items)
        
        if is_valid:
            logger.debug(f"Extraction validated: {result.items}")
            return result
        
        # 3. retry with feedback
        logger.info(f"Extraction invalid ({reason}), starting a retry...")
        retry_result = self._retry_extraction(text, result.items, reason)
        
        # validate the retry result too? No, to avoid an endless loop;
        # but we can check whether it is better
        if len(retry_result.items) > 0:
            # do the retry items have more context?
            avg_original_len = sum(len(i) for i in result.items) / len(result.items)
            avg_retry_len = sum(len(i) for i in retry_result.items) / len(retry_result.items)
            
            if avg_retry_len > avg_original_len * 1.2:  # 20% more text
                logger.info(f"Retry successful: the items are more detailed")
                return retry_result
            else:
                logger.warning(f"Retry not better, keeping the original")
                return result
        
        return result
    
    def _build_prompt(self, text: str) -> str:
        """Builds the user prompt."""
        parts = []
        
        if self.question_context:
            parts.append(self._prompt('user_context', context=self.question_context))
            parts.append("")
        
        parts.append(self._prompt('user_text', text=text))
        
        return "\n".join(parts)
    
    def _parse_response(self, response: str, original_text: str) -> ExtractionResult:
        """Parses the LLM answer."""
        try:
            # extract the JSON from the response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                
                items = data.get('items', [])
                # filter empty items
                items = [i.strip() for i in items if i and i.strip()]
                
                return ExtractionResult(
                    items=items,
                    method='llm',
                    confidence=float(data.get('confidence', 0.8)),
                    original_text=original_text,
                    reasoning=data.get('reasoning')
                )
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON parsing failed: {e}")
        
        # fallback
        return ExtractionResult(
            items=[original_text],
            method='llm_fallback',
            confidence=0.5,
            original_text=original_text,
            reasoning='JSON parsing failed'
        )


class ItemExtractor:
    """
    Main extractor: combines the rule-based and the LLM extraction.
    
    Strategy:
    1. try the rule-based extraction
    2. successful with high confidence -> done
    3. failed or uncertain -> LLM
    """
    
    # threshold for the rule-based extraction
    MIN_RULE_CONFIDENCE = 0.85
    
    def __init__(
        self,
        llm_client,
        db=None,
        question_context: str = "",
        thematic_mode: bool = False
    ):
        """
        Initialises the item extractor.
        
        Args:
            llm_client: LLM client for complex extractions
            db: database for storing (optional)
            question_context: context of the question
            thematic_mode: thematic splitting (more items, details kept)
        """
        self.rule_extractor = RuleBasedExtractor()
        self.llm_extractor = LLMExtractor(llm_client, question_context, thematic_mode=thematic_mode)
        self.db = db
        self.question_context = question_context
        self.thematic_mode = thematic_mode
    
    async def extract_items(
        self,
        text: str,
        force_llm: bool = False,
        thematic: bool = None
    ) -> ExtractionResult:
        """
        Extracts items from a text.
        
        Args:
            text: text to analyse
            force_llm: always use the LLM (even with a clear structure)
            thematic: overrides self.thematic_mode if set
        
        Returns:
            ExtractionResult
        """
        if not text or not text.strip():
            return ExtractionResult(
                items=[],
                method='empty',
                confidence=1.0,
                original_text=""
            )
        
        text = text.strip()
        use_thematic = thematic if thematic is not None else self.thematic_mode
        
        # LLM forced
        if force_llm:
            return await self.llm_extractor.extract(text, thematic=use_thematic)
        
        # try rule-based
        rule_result = self.rule_extractor.extract(text)
        
        if rule_result is not None:
            # single item with high confidence -> return directly
            if rule_result.method == 'single' and rule_result.confidence >= 0.95:
                return rule_result
            
            # several items with high confidence -> return directly
            if rule_result.confidence >= self.MIN_RULE_CONFIDENCE:
                return rule_result
            
            # uncertain -> LLM for validation/improvement
            logger.debug(f"Rule-based extraction uncertain ({rule_result.confidence}), using the LLM")
            llm_result = await self.llm_extractor.extract(text, thematic=use_thematic)
            
            # choose the better result
            if llm_result.confidence > rule_result.confidence:
                return llm_result
            return rule_result
        
        # rule-based failed -> LLM
        return await self.llm_extractor.extract(text, thematic=use_thematic)
    
    def extract_items_sync(
        self,
        text: str,
        force_llm: bool = False,
        thematic: bool = None,
        validate: bool = False
    ) -> ExtractionResult:
        """
        Synchronous version of extract_items.
        
        Args:
            text: text to analyse
            force_llm: always use the LLM
            thematic: thematic splitting (different topics -> separate items)
            validate: LLM validation + retry if the extraction is too abstract
        """
        if not text or not text.strip():
            return ExtractionResult(
                items=[],
                method='empty',
                confidence=1.0,
                original_text=""
            )
        
        text = text.strip()
        use_thematic = thematic if thematic is not None else self.thematic_mode
        
        # with force_llm use the LLM directly (with or without validation)
        if force_llm:
            logger.info(f"LLM forced for: {text[:50]}...")
            if validate and use_thematic:
                return self.llm_extractor.extract_with_validation(text, thematic=use_thematic)
            return self.llm_extractor.extract_sync(text, thematic=use_thematic)
        
        # try rule-based
        rule_result = self.rule_extractor.extract(text)
        
        if rule_result is not None:
            logger.debug(f"Regelbasiert: method={rule_result.method}, confidence={rule_result.confidence}, items={len(rule_result.items)}")
            
            # return directly only with VERY high confidence and a clear structure
            if rule_result.method in ['single', 'single_fallback']:
                # does the text hint at multiple items?
                has_list_indicators = self._has_list_indicators(text)
                
                if has_list_indicators:
                    # possibly a hidden list -> ask the LLM
                    logger.info(f"Single item with list indicators, asking the LLM: {text[:50]}...")
                    if validate and use_thematic:
                        llm_result = self.llm_extractor.extract_with_validation(text, thematic=use_thematic)
                    else:
                        llm_result = self.llm_extractor.extract_sync(text, thematic=use_thematic)
                    if llm_result and len(llm_result.items) > 1:
                        logger.info(f"The LLM found {len(llm_result.items)} items")
                        return llm_result
                    # the LLM found only 1 item too -> keep the rule-based result
                    return rule_result
                else:
                    # no list indicators -> return directly without the LLM
                    return rule_result
            
            # clear structure (newline, semicolon, dash) with high confidence
            if rule_result.confidence >= self.MIN_RULE_CONFIDENCE:
                return rule_result
            
            # uncertain -> LLM for validation/improvement
            logger.info(f"Rule-based extraction uncertain ({rule_result.confidence}), using the LLM")
            if validate and use_thematic:
                llm_result = self.llm_extractor.extract_with_validation(text, thematic=use_thematic)
            else:
                llm_result = self.llm_extractor.extract_sync(text, thematic=use_thematic)
            if llm_result.confidence > rule_result.confidence:
                return llm_result
            return rule_result
        
        # rule-based failed completely -> LLM
        logger.info(f"Rule-based extraction failed, using the LLM for: {text[:50]}...")
        if validate and use_thematic:
            return self.llm_extractor.extract_with_validation(text, thematic=use_thematic)
        return self.llm_extractor.extract_sync(text, thematic=use_thematic)
    
    def _has_list_indicators(self, text: str) -> bool:
        """
        Checks whether the text hints at a hidden list.
        
        Returns:
            True if multiple items are likely
        """
        # short texts are rarely lists
        if len(text) < 50:
            return False
        
        # explicit bullet characters (already detected by a rule, but to be safe)
        if re.search(r'^\s*[-•*]\s', text, re.MULTILINE):
            return True
        if re.search(r'^\s*\d+[\.\)]\s', text, re.MULTILINE):
            return True
        
        # semicolons are strong indicators
        if ';' in text and text.count(';') >= 2:
            return True
        
        # commas with conjunctions (but not in normal sentences):
        # "Tool A, Tool B and Tool C" vs "We use the tool because it is good"
        if ',' in text:
            # count the commas
            comma_count = text.count(',')
            # count "and/or/as well as" after commas
            list_pattern = r',\s*(?:und|oder|sowie|als auch)\s+\w+'
            if comma_count >= 2 and re.search(list_pattern, text, re.IGNORECASE):
                return True
        
        # enumerating conjunctions in certain patterns:
        # "firstly... secondly..." or "a)... b)..."
        if re.search(r'(?:erstens|zweitens|drittens|1\.|2\.|a\)|b\))', text, re.IGNORECASE):
            return True
        
        # "also", "furthermore", "in addition" as separators
        separator_words = r'\b(?:außerdem|darüber hinaus|zusätzlich|ferner|des weiteren|weiterhin)\b'
        if re.search(separator_words, text, re.IGNORECASE):
            return True
        
        return False
    
    async def extract_batch(
        self,
        import_id: int,
        question_id: str,
        column_name: str,
        force_llm: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> ExtractionStats:
        """
        Batch extraction for a complete column.
        
        Args:
            import_id: import id
            question_id: question id
            column_name: column name in the raw data
            force_llm: use the LLM for all texts
            progress_callback: callback(current, total, status_text)
        
        Returns:
            ExtractionStats with statistics
        """
        if self.db is None:
            raise ValueError("Database not configured")
        
        stats = ExtractionStats()
        
        # create the job
        job_id = self.db.create_extraction_job(import_id, question_id, column_name)
        
        try:
            # Responses laden
            responses = self.db.get_raw_responses(import_id)
            stats.total_responses = len(responses)
            
            self.db.update_extraction_job(
                job_id, 
                status='running', 
                total=stats.total_responses
            )
            
            # delete the previous items of this question
            self.db.delete_items(import_id, question_id, column_name)
            
            for idx, response in enumerate(responses):
                row_index = response['row_index']  # items store the data row, see response_items
                data = json.loads(response['data_json'])
                text = str(data.get(column_name, '')).strip()
                
                try:
                    # extract the items
                    result = await self.extract_items(text, force_llm=force_llm)
                    
                    # store in the database
                    for item_idx, item_text in enumerate(result.items, start=1):
                        self.db.save_response_item(
                            import_id=import_id,
                            row_index=row_index,
                            question_id=question_id,
                            column_name=column_name,
                            item_index=item_idx,
                            original_text=text,
                            extracted_text=item_text,
                            extraction_method=result.method,
                            extraction_confidence=result.confidence,
                            llm_reasoning=result.reasoning
                        )
                    
                    stats.total_items += result.item_count
                    stats.methods[result.method] = stats.methods.get(result.method, 0) + 1
                    
                except Exception as e:
                    logger.error(f"Extraction failed for response {row_index}: {e}")
                    stats.errors.append({
                        'row_index': row_index,
                        'error': str(e)
                    })
                
                stats.processed_responses = idx + 1
                
                # Progress-Callback
                if progress_callback:
                    progress_callback(
                        idx + 1, 
                        stats.total_responses,
                        f"Processing {idx + 1}/{stats.total_responses}"
                    )
                
                # update the job status (every 10 items)
                if (idx + 1) % 10 == 0 or idx == stats.total_responses - 1:
                    self.db.update_extraction_job(
                        job_id,
                        processed=stats.processed_responses,
                        items_extracted=stats.total_items
                    )
            
            # compute the statistics
            if stats.processed_responses > 0:
                stats.avg_items_per_response = stats.total_items / stats.processed_responses
            
            # finish the job
            self.db.update_extraction_job(
                job_id,
                status='completed',
                method_stats=stats.methods
            )
            
            logger.info(
                f"Batch extraction finished: {stats.total_items} items "
                f"from {stats.processed_responses} responses"
            )
            
            return stats
            
        except Exception as e:
            self.db.update_extraction_job(job_id, status='failed', error=str(e))
            raise
    
    def extract_batch_sync(
        self,
        import_id: int,
        question_id: str,
        column_name: str,
        force_llm: bool = False,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> ExtractionStats:
        """Synchronous version of extract_batch."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        self.extract_batch(
                            import_id, question_id, column_name,
                            force_llm, progress_callback
                        )
                    )
                    return future.result(timeout=3600)  # 1h max
            else:
                return loop.run_until_complete(
                    self.extract_batch(
                        import_id, question_id, column_name,
                        force_llm, progress_callback
                    )
                )
        except RuntimeError:
            return asyncio.run(
                self.extract_batch(
                    import_id, question_id, column_name,
                    force_llm, progress_callback
                )
            )


# =============================================================================
# HELPERS
# =============================================================================

def preview_extraction(
    texts: List[str],
    extractor: ItemExtractor,
    force_llm: bool = False,
    max_items: int = 5
) -> List[Dict[str, Any]]:
    """
    Creates a preview of the extraction for several texts.
    
    Args:
        texts: list of texts
        extractor: ItemExtractor instance
        force_llm: force the LLM
        max_items: maximum number of texts to process
    
    Returns:
        list of dictionaries with the preview data
    """
    results = []
    
    for i, text in enumerate(texts[:max_items]):
        if not text or not text.strip():
            continue
        
        result = extractor.extract_items_sync(text, force_llm=force_llm)
        
        results.append({
            'index': i,
            'original': text[:150] + '...' if len(text) > 150 else text,
            'items': result.items,
            'items_display': ' | '.join(result.items) if result.items else '(no items)',
            'method': result.method,
            'confidence': result.confidence,
            'item_count': result.item_count
        })
    
    return results
