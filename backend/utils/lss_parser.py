# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
LSS parser for LimeSurvey structure files.

Extracts multilingual question texts, help texts and answer options from
LimeSurvey .lss files (XML format).

Usage:
    parser = LSSParser()
    survey_data = parser.parse_file("survey.lss")
    
    # or: complete questions.yaml automatically
    updated_config = parser.enrich_questions_config(existing_config, survey_data)
"""

import xml.etree.ElementTree as ET
import re
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QuestionText:
    """Multilingual question texts."""
    question: Dict[str, str] = field(default_factory=dict)  # {de: "...", en: "...", fr: "..."}
    help: Dict[str, str] = field(default_factory=dict)  # {de: "...", en: "...", fr: "..."}


@dataclass
class AnswerOption:
    """An answer option with multilingual texts."""
    code: str  # e.g. "AO01", "A1"
    texts: Dict[str, str] = field(default_factory=dict)  # {de: "...", en: "...", fr: "..."}
    sort_order: int = 0


@dataclass
class Question:
    """A question with all its metadata."""
    qid: str  # LimeSurvey internal id
    title: str  # short name (e.g. "ROLE")
    type: str  # LimeSurvey Fragetyp (L, M, Q, T, etc.)
    group_id: str  # group id
    order: int  # order
    texts: QuestionText = field(default_factory=QuestionText)
    answers: List[AnswerOption] = field(default_factory=list)
    subquestions: List['Question'] = field(default_factory=list)
    mandatory: bool = False
    scale_id: int = 0   # subquestions of two-axis arrays (":", ";"): 0 = rows, 1 = columns
    attributes: Dict[str, str] = field(default_factory=dict)   # question_attributes (e.g. equals_num_value)


@dataclass
class QuestionGroup:
    """A question group."""
    gid: str
    order: int
    names: Dict[str, str] = field(default_factory=dict)  # {de: "...", en: "...", fr: "..."}
    descriptions: Dict[str, str] = field(default_factory=dict)


@dataclass
class SurveyData:
    """Complete survey data from an LSS file."""
    survey_id: str
    languages: List[str]
    groups: Dict[str, QuestionGroup]  # gid -> QuestionGroup
    questions: Dict[str, Question]  # title -> Question
    questions_by_qid: Dict[str, Question]  # qid -> Question (for internal lookups)


# LimeSurvey question types to our handler types
LIMESURVEY_TYPE_MAPPING = {
    'L': 'single_choice',  # List (Radio)
    'O': 'single_choice',  # List with comment
    '!': 'single_choice',  # List (Dropdown)
    'M': 'multi_choice',   # Multiple choice
    'P': 'multi_choice',   # Multiple choice with comments
    'Q': 'freetext',       # Multiple Short Text
    'T': 'freetext',       # Long Free Text
    'S': 'freetext',       # Short Free Text
    'U': 'freetext',       # Huge Free Text
    'R': 'ranking',        # Ranking
    'F': 'matrix_likert',  # Array (Flexible Labels)
    'A': 'matrix_likert',  # Array (5 Point Choice)
    'B': 'matrix_likert',  # Array (10 Point Choice)
    'C': 'matrix_likert',  # Array (Yes/No/Uncertain)
    'E': 'matrix_likert',  # Array (Increase/Same/Decrease)
    'H': 'matrix_likert',  # Array by column
    '1': 'matrix_likert',  # Array dual scale
    ';': 'matrix_likert',  # Array (Multi Flexi) (Text)
    ':': 'matrix_likert',  # Array (Multi Flexi) (Numbers)
    'N': 'numeric',        # Numerical Input
    'K': 'numeric',        # Multiple Numerical Input
    'D': 'date',           # Date
    'X': 'display',        # Text display (no input)
    '*': 'equation',       # Equation
    'G': 'single_choice',  # Gender
    'Y': 'single_choice',  # Yes/No
    'I': 'single_choice',  # Language Switch
}


class LSSParser:
    """Parser for LimeSurvey structure (LSS) files."""
    
    def __init__(self):
        self.survey_data: Optional[SurveyData] = None
    
    def parse_file(self, filepath: str) -> SurveyData:
        """
        Parses an LSS file and extracts all survey data.
        
        Args:
            filepath: path of the .lss file
            
        Returns:
            SurveyData with all extracted information
        """
        logger.info(f"Parsing LSS file: {filepath}")
        
        tree = ET.parse(filepath)
        root = tree.getroot()
        
        # survey id and languages
        survey_id = self._get_survey_id(root)
        languages = self._get_languages(root)
        
        logger.info(f"Survey ID: {survey_id}, Languages: {languages}")
        
        # parse the groups
        groups = self._parse_groups(root, languages)
        logger.info(f"Parsed {len(groups)} question groups")
        
        # parse the questions
        questions, questions_by_qid = self._parse_questions(root, languages)
        logger.info(f"Parsed {len(questions)} questions")
        
        # assign the answers to the questions
        self._parse_answers(root, questions_by_qid, languages)
        
        # assign the subquestions to the questions
        self._parse_subquestions(root, questions_by_qid, languages)
        self._parse_attributes(root, questions_by_qid)
        
        self.survey_data = SurveyData(
            survey_id=survey_id,
            languages=languages,
            groups=groups,
            questions=questions,
            questions_by_qid=questions_by_qid
        )
        
        return self.survey_data
    
    def _get_cdata_text(self, element: Optional[ET.Element]) -> str:
        """Extracts the text of an element (with CDATA handling)."""
        if element is None:
            return ""
        return (element.text or "").strip()
    
    def _get_survey_id(self, root: ET.Element) -> str:
        """Extracts the survey id."""
        # search in several possible places
        for surveys in root.findall('.//surveys/rows/row'):
            sid = surveys.find('sid')
            if sid is not None:
                return self._get_cdata_text(sid)
        return "unknown"
    
    def _get_languages(self, root: ET.Element) -> List[str]:
        """Extracts the available languages."""
        languages = []
        for lang in root.findall('.//languages/language'):
            lang_code = self._get_cdata_text(lang)
            if lang_code:
                languages.append(lang_code)
        return languages if languages else ['de']
    
    def _parse_groups(self, root: ET.Element, languages: List[str]) -> Dict[str, QuestionGroup]:
        """Parses the question groups."""
        groups = {}
        
        # group metadata
        for row in root.findall('.//groups/rows/row'):
            gid = self._get_cdata_text(row.find('gid'))
            order = int(self._get_cdata_text(row.find('group_order')) or 0)
            groups[gid] = QuestionGroup(gid=gid, order=order)
        
        # group localisations
        for row in root.findall('.//group_l10ns/rows/row'):
            gid = self._get_cdata_text(row.find('gid'))
            lang = self._get_cdata_text(row.find('language'))
            name = self._get_cdata_text(row.find('group_name'))
            desc = self._get_cdata_text(row.find('description'))
            
            if gid in groups:
                groups[gid].names[lang] = name
                # remove HTML tags from the description
                groups[gid].descriptions[lang] = self._strip_html(desc)
        
        return groups
    
    def _parse_questions(self, root: ET.Element, languages: List[str]) -> tuple:
        """Parses the questions."""
        questions = {}  # title -> Question
        questions_by_qid = {}  # qid -> Question
        
        # question metadata
        for row in root.findall('.//questions/rows/row'):
            qid = self._get_cdata_text(row.find('qid'))
            parent_qid = self._get_cdata_text(row.find('parent_qid'))
            
            # only main questions (parent_qid == 0)
            if parent_qid and parent_qid != '0':
                continue
            
            title = self._get_cdata_text(row.find('title'))
            qtype = self._get_cdata_text(row.find('type'))
            gid = self._get_cdata_text(row.find('gid'))
            order = int(self._get_cdata_text(row.find('question_order')) or 0)
            mandatory = self._get_cdata_text(row.find('mandatory')) == 'Y'
            
            question = Question(
                qid=qid,
                title=title,
                type=qtype,
                group_id=gid,
                order=order,
                mandatory=mandatory
            )
            
            questions[title] = question
            questions_by_qid[qid] = question
        
        # question localisations
        for row in root.findall('.//question_l10ns/rows/row'):
            qid = self._get_cdata_text(row.find('qid'))
            lang = self._get_cdata_text(row.find('language'))
            question_text = self._get_cdata_text(row.find('question'))
            help_text = self._get_cdata_text(row.find('help'))
            
            if qid in questions_by_qid:
                q = questions_by_qid[qid]
                q.texts.question[lang] = self._strip_html(question_text)
                if help_text:
                    q.texts.help[lang] = self._strip_html(help_text)
        
        return questions, questions_by_qid
    
    def _parse_answers(self, root: ET.Element, questions_by_qid: Dict[str, Question], languages: List[str]):
        """Parses the answer options and assigns them to the questions."""
        # answer metadata: aid -> (qid, code, sort_order)
        answer_meta = {}
        for row in root.findall('.//answers/rows/row'):
            aid = self._get_cdata_text(row.find('aid'))
            qid = self._get_cdata_text(row.find('qid'))
            code = self._get_cdata_text(row.find('code'))
            sort_order = int(self._get_cdata_text(row.find('sortorder')) or 0)
            answer_meta[aid] = (qid, code, sort_order)
        
        # answer localisations
        answer_texts = {}  # aid -> {lang: text}
        for row in root.findall('.//answer_l10ns/rows/row'):
            aid = self._get_cdata_text(row.find('aid'))
            lang = self._get_cdata_text(row.find('language'))
            answer_text = self._get_cdata_text(row.find('answer'))
            
            if aid not in answer_texts:
                answer_texts[aid] = {}
            answer_texts[aid][lang] = answer_text
        
        # assign the answers to the questions
        for aid, (qid, code, sort_order) in answer_meta.items():
            if qid in questions_by_qid and aid in answer_texts:
                answer = AnswerOption(
                    code=code,
                    texts=answer_texts[aid],
                    sort_order=sort_order
                )
                questions_by_qid[qid].answers.append(answer)
        
        # sort the answers by sort_order
        for q in questions_by_qid.values():
            q.answers.sort(key=lambda a: a.sort_order)
    
    def _parse_subquestions(self, root: ET.Element, questions_by_qid: Dict[str, Question], languages: List[str]):
        """Parses subquestions (for matrix questions)."""
        # subquestion metadata
        subq_meta = {}  # qid -> (parent_qid, title, order)
        for row in root.findall('.//subquestions/rows/row'):
            qid = self._get_cdata_text(row.find('qid'))
            parent_qid = self._get_cdata_text(row.find('parent_qid'))
            title = self._get_cdata_text(row.find('title'))
            order = int(self._get_cdata_text(row.find('question_order')) or 0)
            scale_id = int(self._get_cdata_text(row.find('scale_id')) or 0)
            
            if parent_qid and parent_qid != '0':
                subq_meta[qid] = (parent_qid, title, order, scale_id)
        
        # subquestion localisations
        subq_texts = {}  # qid -> {lang: text}
        for row in root.findall('.//question_l10ns/rows/row'):
            qid = self._get_cdata_text(row.find('qid'))
            if qid in subq_meta:
                lang = self._get_cdata_text(row.find('language'))
                question_text = self._get_cdata_text(row.find('question'))
                
                if qid not in subq_texts:
                    subq_texts[qid] = {}
                subq_texts[qid][lang] = self._strip_html(question_text)
        
        # assign the subquestions to the main questions
        for qid, (parent_qid, title, order, scale_id) in subq_meta.items():
            if parent_qid in questions_by_qid:
                subq = Question(
                    qid=qid,
                    title=title,
                    type='subquestion',
                    group_id='',
                    order=order,
                    scale_id=scale_id,
                )
                if qid in subq_texts:
                    subq.texts.question = subq_texts[qid]
                questions_by_qid[parent_qid].subquestions.append(subq)
        
        # sort the subquestions by order
        for q in questions_by_qid.values():
            q.subquestions.sort(key=lambda sq: sq.order)
    
    def _parse_attributes(self, root: ET.Element, questions_by_qid: Dict[str, Question]):
        """Question attributes (``question_attributes``); language-dependent ones keep the first value."""
        for row in root.findall('.//question_attributes/rows/row'):
            qid = self._get_cdata_text(row.find('qid'))
            name = self._get_cdata_text(row.find('attribute'))
            value = self._get_cdata_text(row.find('value'))
            if qid in questions_by_qid and name:
                questions_by_qid[qid].attributes.setdefault(name, value)

    def _strip_html(self, text: str) -> str:
        """Removes HTML tags from text."""
        if not text:
            return ""
        # simple HTML tag removal
        clean = re.sub(r'<[^>]+>', '', text)
        # normalise repeated spaces/line breaks
        clean = re.sub(r'\s+', ' ', clean)
        return clean.strip()
    
    def get_handler_type(self, limesurvey_type: str) -> str:
        """Maps a LimeSurvey question type to our handler type."""
        return LIMESURVEY_TYPE_MAPPING.get(limesurvey_type, 'single_choice')
    
    def enrich_questions_config(
        self, 
        existing_config: Dict[str, Any],
        survey_data: Optional[SurveyData] = None,
        title_to_id_mapping: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Completes an existing questions.yaml configuration with LSS data.
        
        Args:
            existing_config: existing questions.yaml as a dict
            survey_data: parsed LSS data (or self.survey_data)
            title_to_id_mapping: mapping of LSS titles to configuration ids,
                                 e.g. {"ROLE": "role"}
                                 
        Returns:
            the updated configuration
        """
        if survey_data is None:
            survey_data = self.survey_data
        
        if survey_data is None:
            raise ValueError("No survey data available. Call parse_file() first.")
        
        config = existing_config.copy()
        question_groups = config.get('question_groups', {})
        
        # without a given mapping, try an automatic matching
        if title_to_id_mapping is None:
            title_to_id_mapping = self._auto_match_questions(question_groups, survey_data)
            logger.info(f"Auto-matched {len(title_to_id_mapping)} questions")
        
        # enrich the questions
        for lss_title, config_id in title_to_id_mapping.items():
            if lss_title not in survey_data.questions:
                logger.warning(f"LSS question '{lss_title}' not found in survey data")
                continue
            
            if config_id not in question_groups:
                logger.warning(f"Config question '{config_id}' not found in config")
                continue
            
            lss_q = survey_data.questions[lss_title]
            config_q = question_groups[config_id]
            
            # add the full question text (multilingual)
            if lss_q.texts.question:
                config_q['full_question'] = lss_q.texts.question.copy()
            
            # add the help text if missing or incomplete
            if lss_q.texts.help:
                if 'help' not in config_q:
                    config_q['help'] = {}
                for lang, text in lss_q.texts.help.items():
                    if lang not in config_q['help'] or not config_q['help'][lang]:
                        config_q['help'][lang] = text
            
            # answer options for single/multiple choice
            if lss_q.answers and self.get_handler_type(lss_q.type) in ['single_choice', 'multi_choice']:
                if 'options' not in config_q:
                    config_q['options'] = {}
                
                for answer in lss_q.answers:
                    if 'de' in answer.texts:
                        option_key = answer.code
                        config_q['options'][option_key] = answer.texts.copy()
            
            # subquestions for matrix questions
            if lss_q.subquestions:
                if 'subitems' not in config_q:
                    config_q['subitems'] = {}
                
                for subq in lss_q.subquestions:
                    if subq.texts.question:
                        config_q['subitems'][subq.title] = subq.texts.question.copy()
            
            # add the LSS metadata
            config_q['lss_metadata'] = {
                'qid': lss_q.qid,
                'title': lss_q.title,
                'type': lss_q.type,
                'handler_type': self.get_handler_type(lss_q.type)
            }
        
        return config
    
    def _auto_match_questions(
        self, 
        question_groups: Dict[str, Any], 
        survey_data: SurveyData
    ) -> Dict[str, str]:
        """
        Tries an automatic matching between LSS titles and configuration ids.
        
        Strategies:
        1. exact match (case-insensitive)
        2. a column name in columns contains the LSS title
        3. similarity of the question texts
        """
        mapping = {}
        
        for config_id, config_q in question_groups.items():
            # strategy 1: the configuration id equals the LSS title
            for lss_title in survey_data.questions:
                if lss_title.lower() == config_id.lower():
                    mapping[lss_title] = config_id
                    break
                
                # strategy 2: the LSS title in the column name
                columns = config_q.get('columns', [])
                for col in columns:
                    if isinstance(col, str) and lss_title.lower() in col.lower():
                        mapping[lss_title] = config_id
                        break
        
        return mapping
    
    def generate_questions_yaml(
        self, 
        survey_data: Optional[SurveyData] = None,
        id_prefix: str = ""
    ) -> Dict[str, Any]:
        """
        Generates a complete questions.yaml from LSS data.
        
        Args:
            survey_data: parsed LSS data
            id_prefix: prefix of the generated ids
            
        Returns:
            configuration in the questions.yaml format
        """
        if survey_data is None:
            survey_data = self.survey_data
        
        if survey_data is None:
            raise ValueError("No survey data available.")
        
        config = {
            'settings': {
                'survey_metadata': {
                    'survey_id': survey_data.survey_id,
                    'languages': survey_data.languages
                },
                'report_groups': {}
            },
            'question_groups': {}
        }
        
        # groups to report_groups
        for gid, group in sorted(survey_data.groups.items(), key=lambda x: x[1].order):
            group_id = f"group_{gid}"
            config['settings']['report_groups'][group_id] = {
                'order': group.order,
                'label': group.names,
                'description': group.descriptions,
                'questions': []
            }
        
        # questions to question_groups
        for title, q in survey_data.questions.items():
            q_id = f"{id_prefix}{title.lower()}" if id_prefix else title.lower()
            handler_type = self.get_handler_type(q.type)
            
            q_config = {
                'id': q_id,
                'label': q.texts.question.copy() if q.texts.question else {'de': title},
                'full_question': q.texts.question.copy() if q.texts.question else {},
                'type': handler_type,
                'handler': handler_type,
                'lss_metadata': {
                    'qid': q.qid,
                    'title': title,
                    'type': q.type
                }
            }
            
            # Help-Text
            if q.texts.help:
                q_config['help'] = q.texts.help.copy()
            
            # answer options
            if q.answers:
                q_config['options'] = {
                    a.code: a.texts.copy() for a in q.answers
                }
            
            # Unterfragen
            if q.subquestions:
                q_config['subitems'] = {
                    sq.title: sq.texts.question.copy() for sq in q.subquestions
                }
            
            config['question_groups'][q_id] = q_config
            
            # add to the group
            group_id = f"group_{q.group_id}"
            if group_id in config['settings']['report_groups']:
                config['settings']['report_groups'][group_id]['questions'].append(q_id)
        
        return config
    
    def print_summary(self, survey_data: Optional[SurveyData] = None):
        """Prints a summary of the parsed data."""
        if survey_data is None:
            survey_data = self.survey_data
        
        if survey_data is None:
            print("No survey data loaded.")
            return
        
        print(f"\n{'='*60}")
        print(f"LSS Survey Summary")
        print(f"{'='*60}")
        print(f"Survey ID: {survey_data.survey_id}")
        print(f"Languages: {', '.join(survey_data.languages)}")
        print(f"Groups: {len(survey_data.groups)}")
        print(f"Questions: {len(survey_data.questions)}")
        
        print(f"\n{'='*60}")
        print("Questions by Type:")
        print(f"{'='*60}")
        
        type_counts = {}
        for q in survey_data.questions.values():
            handler = self.get_handler_type(q.type)
            type_counts[handler] = type_counts.get(handler, 0) + 1
        
        for handler, count in sorted(type_counts.items()):
            print(f"  {handler}: {count}")
        
        print(f"\n{'='*60}")
        print("Questions Detail:")
        print(f"{'='*60}")
        
        for title, q in sorted(survey_data.questions.items(), key=lambda x: x[1].order):
            de_text = (q.texts.question.get('de') or next(iter(q.texts.question.values()), ''))[:60]
            print(f"\n{title} ({q.type} -> {self.get_handler_type(q.type)})")
            print(f"  DE: {de_text}...")
            if q.answers:
                print(f"  Answers: {len(q.answers)}")
            if q.subquestions:
                print(f"  Subquestions: {len(q.subquestions)}")


def parse_lss_file(filepath: str) -> SurveyData:
    """Convenience function to parse an LSS file."""
    parser = LSSParser()
    return parser.parse_file(filepath)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python lss_parser.py <lss_file>")
        sys.exit(1)
    
    parser = LSSParser()
    data = parser.parse_file(sys.argv[1])
    parser.print_summary(data)
