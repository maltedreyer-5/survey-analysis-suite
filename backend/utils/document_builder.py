# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Document builder for publication documents.

Reads documents.yaml (of the active survey, else the shared one in config/),
assembles Markdown source files into Word documents, translates them paragraph
by paragraph with the LLM and gives all documents a cover, background, table of
contents and footer.

Uses add_markdown_to_document() of export_word.py for the Markdown-to-Word
conversion.
"""

import hashlib
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import yaml
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

from utils.export_word import add_markdown_to_document

logger = logging.getLogger(__name__)



def _default_documents_config() -> Path:
    """The documents.yaml of the active survey (publication documents are optional)."""
    from core import get_active_config_dir
    return Path(get_active_config_dir()) / "documents.yaml"


def _active_survey_id():
    try:
        from core import get_active_survey_id
        return get_active_survey_id()
    except Exception:
        return None


def _run_survey_id(run_dir: Path):
    """The survey a pipeline or re-export run belongs to (from its exports), None if unknown."""
    import json
    for name, key in (("dashboard_data.json", ("metadata", "survey_id")), ("llm_usage.json", ("survey_id",))):
        f = run_dir / name
        if f.exists():
            try:
                value = json.loads(f.read_text(encoding="utf-8"))
                for k in key:
                    value = value.get(k) if isinstance(value, dict) else None
                if value:
                    return value
            except (OSError, ValueError):
                pass
    return None


def _output_dir() -> Path:
    from core import OUTPUT_DIR
    return Path(OUTPUT_DIR)


class DocumentBuilder:
    """Builds publication documents from Markdown sources."""
    
    def __init__(
        self,
        config_path: str = None,
        db_path: str = None
    ):
        # defaults independent of the working directory: the documents.yaml of the
        # active survey and the open database
        self.config_path = Path(config_path) if config_path else _default_documents_config()
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"No documents.yaml in {self.config_path.parent}: publication documents are optional "
                f"and need a documents.yaml in the directory of the survey (see docs/operations.md)")
        if db_path:
            self.db_path = Path(db_path)
        else:
            from utils.persistence import get_db_path
            self.db_path = get_db_path()
        self.config = self._load_config()
        # Markdown sources: relative to the documents.yaml, not to the working directory
        source_dir = Path(self.config.get('source_dir', 'docs'))
        self.source_dir = source_dir if source_dir.is_absolute() else self.config_path.parent / source_dir
        # language the Markdown sources are written in (documents.yaml; default: the
        # source language of the active survey)
        self.source_language = str(self.config.get('source_language') or self._survey_source_language())
    
    @staticmethod
    def _survey_source_language() -> str:
        try:
            from core import state
            from utils.languages import get_source_language
            return get_source_language(state.config)
        except Exception:
            return 'en'

    def _load_config(self) -> dict:
        """Loads the document configuration."""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    def _translate_via_service(self, text: str, target_lang: str, kind: str) -> Optional[str]:
        """
        Translation through the single translation path (utils/translation_service.py)
        with the context package of the loaded survey and the glossary that
        comes with it. ``kind`` is doc_paragraph, doc_table or doc_heading.
        Returns None if no usable translation came back. The builder keeps
        its own cache per document position, so the service is used without
        its translation memory.
        """
        from utils import prompts
        from utils.llm import get_llm_client
        from utils.translation_service import TranslationContext, TranslationService
        config = prompts.current_config() or {}
        service = TranslationService(get_llm_client(), config, None)
        translated, ok = service.translate_with_status(
            text, target_lang, TranslationContext.from_config(config, kind=kind),
            source_lang=self.source_language)
        return translated if ok else None
    
    # =========================================================================
    # COVER AND PLACEHOLDERS
    # =========================================================================
    
    def _cover_text(self, value, lang: str, kind: str) -> str:
        """A text of the cover in ``lang``: given in documents.yaml, else translated from the source language."""
        src = self.source_language
        if isinstance(value, dict):
            if value.get(lang):
                return str(value[lang])
            source = value.get(src) or next((v for v in value.values() if v), '')
        else:
            source = str(value or '')
        if not source or lang == src:
            return str(source)
        return self._translate_via_service(str(source), lang, kind) or str(source)

    def _resolve_cover(self, doc_config: dict, lang: str) -> str:
        """
        Resolves the cover placeholders of a document.
        
        Placeholders: {title}, {subtitle}, {purpose}, {year}, {organization}, {doi_url}
        """
        cover_file = self.config['shared_sections']['cover']
        cover_template = (self.source_dir / cover_file).read_text(encoding='utf-8')
        
        # title: from doc_config or from the global metadata
        title_dict = doc_config.get('title', self.config.get('metadata', {}).get('survey_title', {}))
        from .languages import localized
        src = self.source_language
        title = self._cover_text(title_dict, lang, 'doc_heading')
        subtitle = self._cover_text(doc_config.get('subtitle') or {}, lang, 'doc_heading')
        purpose = self._cover_text(doc_config.get('purpose') or {}, lang, 'doc_paragraph')
        doi = self.config.get('doi') or {}
        doi_url = localized(doi, lang, src, default='') if isinstance(doi, dict) else str(doi)
        metadata = self.config.get('metadata') or {}
        year = str(metadata.get('year') or __import__('datetime').date.today().year)
        organization = localized(metadata.get('organization') or '', lang, src, default='') \
            if isinstance(metadata.get('organization'), dict) else str(metadata.get('organization') or '')
        
        return cover_template.format(
            title=title,
            subtitle=subtitle,
            purpose=purpose,
            doi_url=doi_url,
            organization=organization,
            year=year
        )
    
    def _resolve_sections(self, doc_config: dict, lang: str) -> list[tuple[str, str, int]]:
        """
        Resolves the list of sections, including groups with heading_offset.
        
        Supports:
            - 'shared:cover'         -> (cover.md, content, 0)
            - 'shared:background'    -> (background.md, content, 0)
            - 'file.md'              -> (file.md, content, 0)
            - group:                 -> group heading (H1) + sub-sections with an offset
                title: {de: ..., fr: ..., en: ...}
                heading_offset: N
                sections: [...]
        
        Returns:
            list of (filename, markdown_content, heading_offset) tuples;
            heading_offset=0 means no shift.
        """
        sections = []
        
        for section_ref in doc_config.get('sections', []):
            # string reference (simple section or shared)
            if isinstance(section_ref, str):
                resolved = self._resolve_single_section(section_ref, doc_config, lang)
                if resolved:
                    fname, content = resolved
                    sections.append((fname, content, 0))
            
            # dict reference (group:)
            elif isinstance(section_ref, dict) and 'group' in section_ref:
                group = section_ref['group']
                title_dict = group.get('title', {})
                title_text = title_dict.get(lang, title_dict.get('de', ''))
                offset = group.get('heading_offset', 0)
                
                # group heading as its own mini section (H1)
                if title_text:
                    sections.append((
                        f'_group_{title_text}.md',
                        f'# {title_text}\n',
                        0  # the group title stays H1
                    ))
                
                # sub-sections with an offset
                for sub_ref in group.get('sections', []):
                    resolved = self._resolve_single_section(sub_ref, doc_config, lang)
                    if resolved:
                        fname, content = resolved
                        sections.append((fname, content, offset))
        
        return sections
    
    def _resolve_single_section(
        self, section_ref: str, doc_config: dict, lang: str
    ) -> Optional[tuple[str, str]]:
        """Resolves a single section reference."""
        if section_ref == 'shared:cover':
            return ('cover.md', self._resolve_cover(doc_config, lang))
        elif section_ref.startswith('shared:'):
            key = section_ref.split(':')[1]
            filename = self.config['shared_sections'].get(key, '')
            if not filename:
                logger.warning(f"Shared section '{key}' not defined in the configuration")
                return None
            path = self.source_dir / filename
            if path.exists():
                return (filename, path.read_text(encoding='utf-8'))
            logger.warning(f"Shared section not found: {path}")
            return None
        else:
            path = self.source_dir / section_ref
            if path.exists():
                return (section_ref, path.read_text(encoding='utf-8'))
            logger.warning(f"Section file not found: {path}")
            return None
    
    # =========================================================================
    # ASSEMBLING THE WORD DOCUMENT
    # =========================================================================
    
    def _create_base_document(self, lang: str, doc_config: dict = None) -> Document:
        """Creates a new document with default styles and a footer."""
        doc = Document()
        
        style = doc.styles['Normal']
        style.font.name = 'Calibri'
        style.font.size = Pt(11)
        
        # heading styles with a clear size hierarchy
        heading_sizes = {1: 18, 2: 16, 3: 14, 4: 12}
        for level, size in heading_sizes.items():
            hs = doc.styles[f'Heading {level}']
            hs.font.name = 'Calibri'
            hs.font.bold = True
            hs.font.size = Pt(size)
        
        self._add_page_footer(doc, lang, doc_config)
        return doc
    
    def _add_toc(self, doc: Document, lang: str, max_level: int = 2):
        """
        Adds a table of contents (levels 1 to max_level).
        
        Word updates the TOC when the document is opened (Ctrl+A, F9).
        The TOC heading uses 'TOC Heading' (outlineLevel=9), so it does not
        appear as an entry of its own.
        """
        toc_titles = {
            'de': 'Inhaltsverzeichnis',
            'fr': 'Table des matières',
            'en': 'Table of Contents',
            'it': 'Indice',
        }
        # TOC heading style: looks like Heading 1, but has outlineLevel=9
        # -> does NOT appear in the TOC field
        toc_heading = doc.add_paragraph(toc_titles.get(lang, toc_titles['en']))
        toc_heading.style = doc.styles['TOC Heading']
        
        para = doc.add_paragraph()
        fld_simple = OxmlElement('w:fldSimple')
        fld_simple.set(qn('w:instr'), f'TOC \\o "1-{max_level}" \\h \\z \\u')
        
        run_element = OxmlElement('w:r')
        text_element = OxmlElement('w:t')
        placeholders = {
            'de': 'Bitte Strg+A, dann F9 drücken um das Verzeichnis zu aktualisieren.',
            'fr': 'Appuyez sur Ctrl+A, puis F9 pour mettre à jour.',
            'en': 'Press Ctrl+A, then F9 to update the table of contents.',
            'it': "Premere Ctrl+A, poi F9 per aggiornare l'indice."
        }
        text_element.text = placeholders.get(lang, placeholders['en'])
        run_element.append(text_element)
        fld_simple.append(run_element)
        para._p.append(fld_simple)
        
        doc.add_page_break()
    
    def _build_from_sections(
        self, sections: list, lang: str,
        include_toc: bool = True, doc_config: dict = None
    ) -> Document:
        """
        Builds a Word document from Markdown sections.
        
        Accepts 2-tuples (filename, content) or 3-tuples
        (filename, content, heading_offset).
        
        heading_offset > 0 moves all Markdown headings down by N levels
        (# -> ##, ## -> ###, etc.), capped at H4.
        """
        doc = self._create_base_document(lang, doc_config)
        
        for section_idx, section_data in enumerate(sections):
            # 2-tuples or 3-tuples
            if len(section_data) == 3:
                filename, content, heading_offset = section_data
            else:
                filename, content = section_data
                heading_offset = 0
            
            # cover section: own rendering (Title/Subtitle instead of a heading)
            if filename == 'cover.md':
                self._add_cover(doc, content, lang)
                doc.add_page_break()
                if include_toc:
                    toc_depth = (doc_config or {}).get('toc_max_level', 2)
                    self._add_toc(doc, lang, max_level=toc_depth)
                continue
            
            # apply the heading offset
            if heading_offset > 0:
                content = self._apply_heading_offset(content, heading_offset)
            
            # remember the number of paragraphs before inserting
            para_count_before = len(doc.paragraphs)
            
            add_markdown_to_document(doc, content, lang=lang)
            
            # page break on the first new paragraph (not after the cover/TOC)
            if section_idx > 1 and len(doc.paragraphs) > para_count_before:
                first_new_para = doc.paragraphs[para_count_before]
                first_new_para.paragraph_format.page_break_before = True
        
        return doc
    
    @staticmethod
    def _apply_heading_offset(markdown_text: str, offset: int) -> str:
        """
        Moves all Markdown headings down by offset levels.
        
        # Title   + offset=1 -> ## Title
        ## Sub     + offset=1 -> ### Sub
        #### Deep  + offset=1 -> #### Deep  (capped at H4)
        
        Headings inside code blocks (```) are NOT changed.
        """
        if offset <= 0:
            return markdown_text
        
        lines = markdown_text.split('\n')
        result = []
        in_code_block = False
        
        for line in lines:
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
                result.append(line)
                continue
            
            if not in_code_block and re.match(r'^#{1,4}[ \t]+', line):
                match = re.match(r'^(#{1,4})([ \t]+)', line)
                new_level = min(len(match.group(1)) + offset, 4)
                result.append('#' * new_level + match.group(2) + line[match.end():])
            else:
                result.append(line)
        
        return '\n'.join(result)
    
    def _add_cover(self, doc: Document, cover_content: str, lang: str):
        """
        Renders the title page with the Word Title/Subtitle styles instead of headings.
        
        Title and Subtitle have no outlineLevel and therefore do NOT appear in
        the table of contents. The cover is parsed from the resolved Markdown
        (with the placeholders replaced).
        """
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        
        for line in cover_content.strip().split('\n'):
            stripped = line.strip()
            if not stripped:
                continue
            
            # # Title → Word 'Title' Style
            if stripped.startswith('# ') and not stripped.startswith('## '):
                title_text = stripped.lstrip('#').strip()
                para = doc.add_paragraph(title_text)
                para.style = doc.styles['Title']
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # ## Subtitle → Word 'Subtitle' Style
            elif stripped.startswith('## '):
                subtitle_text = stripped.lstrip('#').strip()
                para = doc.add_paragraph(subtitle_text)
                para.style = doc.styles['Subtitle']
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # --- separator -> empty paragraph as spacing
            elif stripped.replace(' ', '') in ('---', '***', '___'):
                doc.add_paragraph()
            
            # **bold** -> paragraph in bold
            elif stripped.startswith('**') and stripped.endswith('**'):
                text = stripped.strip('*').strip()
                para = doc.add_paragraph()
                run = para.add_run(text)
                run.bold = True
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            
            # normal text
            else:
                para = doc.add_paragraph(stripped)
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    def _add_page_footer(
        self, doc: Document, lang: str, doc_config: dict = None
    ):
        """
        Adds a footer: title + subtitle on the left, page number on the right.
        
        Layout: "Title - subtitle                    Page X of Y"
        """
        footer_labels = {
            'de': ("Seite ", " von "),
            'fr': ("Page ", " sur "),
            'en': ("Page ", " of ")
        }
        prefix, separator = footer_labels.get(lang, footer_labels['de'])
        
        section = doc.sections[0]
        footer = section.footer
        footer.is_linked_to_previous = False
        
        para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        para.clear()
        
        # right-aligned tab stop at the right margin
        # A4: 21cm, default margins: 2.54cm left + 2.54cm right -> ~15.92cm text width
        from docx.shared import Cm
        from docx.enum.text import WD_TAB_ALIGNMENT
        page_width = section.page_width
        left_margin = section.left_margin
        right_margin = section.right_margin
        text_width = page_width - left_margin - right_margin
        
        tab_stops = para.paragraph_format.tab_stops
        tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
        
        # === left: title - subtitle ===
        if doc_config:
            title_dict = doc_config.get('title', {})
            subtitle_dict = doc_config.get('subtitle', {})
            title = title_dict.get(lang, title_dict.get('de', ''))
            subtitle = subtitle_dict.get(lang, subtitle_dict.get('de', ''))
            
            footer_text = title
            if subtitle:
                footer_text += f" – {subtitle}"
            
            title_run = para.add_run(footer_text)
            title_run.font.size = Pt(8)
            title_run.font.color.rgb = RGBColor(128, 128, 128)
        
        # tab -> to the right
        tab_run = para.add_run('\t')
        tab_run.font.size = Pt(9)
        
        # === right: page X of Y ===
        page_run = para.add_run(prefix)
        page_run.font.size = Pt(9)
        page_run.font.color.rgb = RGBColor(128, 128, 128)
        
        # PAGE field
        self._add_word_field(para, ' PAGE ', '1')
        
        sep_run = para.add_run(separator)
        sep_run.font.size = Pt(9)
        sep_run.font.color.rgb = RGBColor(128, 128, 128)
        
        # NUMPAGES field
        self._add_word_field(para, ' NUMPAGES ', '1')
    
    @staticmethod
    def _add_word_field(para, field_code: str, placeholder: str = '1'):
        """
        Inserts a Word field (PAGE, NUMPAGES etc.) with the fldChar approach.
        
        fldChar (begin/separate/end) is more reliable than fldSimple, because
        Word keeps the RunProperties of every single run when resolving the field.
        """
        def _make_rpr():
            """Consistent RunProperties for field runs."""
            rpr = OxmlElement('w:rPr')
            sz = OxmlElement('w:sz')
            sz.set(qn('w:val'), '18')  # 9pt
            szCs = OxmlElement('w:szCs')
            szCs.set(qn('w:val'), '18')
            color = OxmlElement('w:color')
            color.set(qn('w:val'), '808080')
            rpr.append(sz)
            rpr.append(szCs)
            rpr.append(color)
            return rpr
        
        # 1. Begin-Run
        r_begin = OxmlElement('w:r')
        r_begin.append(_make_rpr())
        fld_begin = OxmlElement('w:fldChar')
        fld_begin.set(qn('w:fldCharType'), 'begin')
        r_begin.append(fld_begin)
        para._p.append(r_begin)
        
        # 2. Instruction-Run
        r_instr = OxmlElement('w:r')
        r_instr.append(_make_rpr())
        instr_text = OxmlElement('w:instrText')
        instr_text.set(qn('xml:space'), 'preserve')
        instr_text.text = field_code
        r_instr.append(instr_text)
        para._p.append(r_instr)
        
        # 3. Separate-Run
        r_sep = OxmlElement('w:r')
        r_sep.append(_make_rpr())
        fld_sep = OxmlElement('w:fldChar')
        fld_sep.set(qn('w:fldCharType'), 'separate')
        r_sep.append(fld_sep)
        para._p.append(r_sep)
        
        # 4. result run (placeholder, replaced when the document is opened)
        r_result = OxmlElement('w:r')
        r_result.append(_make_rpr())
        t = OxmlElement('w:t')
        t.text = placeholder
        r_result.append(t)
        para._p.append(r_result)
        
        # 5. End-Run
        r_end = OxmlElement('w:r')
        r_end.append(_make_rpr())
        fld_end = OxmlElement('w:fldChar')
        fld_end.set(qn('w:fldCharType'), 'end')
        r_end.append(fld_end)
        para._p.append(r_end)
    
    # =========================================================================
    # TRANSLATION WITH A DATABASE CACHE
    # =========================================================================
    
    @staticmethod
    def _text_hash(text: str) -> str:
        """Creates a short hash for change detection."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    
    def _get_cached_translation(
        self, doc_id: str, section_file: str, para_idx: int,
        target_lang: str, source_hash: str
    ) -> Optional[str]:
        """Checks whether a current translation is in the cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT translated_text FROM document_translations
                WHERE document_id = ? AND section_file = ? AND paragraph_index = ?
                  AND target_lang = ? AND source_hash = ?
            """, (doc_id, section_file, para_idx, target_lang, source_hash))
            row = cursor.fetchone()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.warning(f"Cache lookup failed: {e}")
            return None
    
    def _get_cached_translation_any_doc(
        self, section_file: str, para_idx: int,
        target_lang: str, source_hash: str
    ) -> Optional[str]:
        """
        Checks whether ANY document has the same translation in the cache.
        
        Useful for shared_sections such as background.md, which are identical in
        all documents: a translation made for one document is reused by the others.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT translated_text, document_id FROM document_translations
                WHERE section_file = ? AND paragraph_index = ?
                  AND target_lang = ? AND source_hash = ?
                LIMIT 1
            """, (section_file, para_idx, target_lang, source_hash))
            row = cursor.fetchone()
            conn.close()
            if row:
                logger.debug(
                    f"Cross-document cache hit: {section_file}[{para_idx}] → {target_lang} "
                    f"(from doc_id='{row[1]}')"
                )
                return row[0]
            return None
        except Exception as e:
            logger.warning(f"Cross-document cache lookup failed: {e}")
            return None
    
    def _save_translation(
        self, doc_id: str, section_file: str, para_idx: int,
        source_lang: str, target_lang: str, source_text: str,
        source_hash: str, translated_text: str
    ):
        """Stores a translation in the cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO document_translations
                (document_id, section_file, paragraph_index, source_lang,
                 target_lang, source_text, source_hash, translated_text, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (doc_id, section_file, para_idx, source_lang, target_lang,
                  source_text, source_hash, translated_text,
                  datetime.now().isoformat()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Storing in the cache failed: {e}")
    
    def _translate_paragraph(
        self, text: str, target_lang: str,
        doc_id: str, section_file: str, para_idx: int
    ) -> str:
        """
        Translates a paragraph with the LLM, using the database cache.
        
        1. empty-text guard: return empty/trivial inputs at once
        2. compute the hash -> check the cache (this document) -> return a hit
        3. shared sections: check the cross-document cache (same text, other document)
        4. otherwise: LLM translation with the glossary as terminology reference
        5. clean the result (no heading markers etc.)
        6. cache the result
        """
        # guard: do not translate empty, trivial or purely technical texts
        if not text or not text.strip():
            return text
        stripped_check = text.strip()
        # pure formatting (only Markdown markers, asterisks, underscores)
        if re.match(r'^[\s*_`#\-=|>~\[\](){}\\/:;,.!?@&$%^+<>\d]+$', stripped_check):
            return text
        source_hash = self._text_hash(text)
        
        # 1. check the cache (this document)
        cached = self._get_cached_translation(
            doc_id, section_file, para_idx, target_lang, source_hash
        )
        if cached:
            logger.debug(f"Cache-Hit: {doc_id}/{section_file}[{para_idx}] → {target_lang}")
            return self._sanitize_translated_paragraph(cached, text)
        
        # 2. cross-document cache: shared sections (e.g. background.md) may have
        #    been translated for another document already
        shared_files = set()
        for key, fname in self.config.get('shared_sections', {}).items():
            shared_files.add(fname)
        
        if section_file in shared_files:
            cross_cached = self._get_cached_translation_any_doc(
                section_file, para_idx, target_lang, source_hash
            )
            if cross_cached:
                # cache the translation for this document too
                self._save_translation(
                    doc_id, section_file, para_idx,
                    self.source_language, target_lang, text, source_hash, cross_cached
                )
                logger.info(
                    f"Cross-document reuse: {doc_id}/{section_file}[{para_idx}] → {target_lang}"
                )
                return self._sanitize_translated_paragraph(cross_cached, text)
        
        # 3. Translation through the single translation path
        try:
            translated = self._translate_via_service(text, target_lang, "doc_paragraph")
        except Exception as e:
            logger.error(f"Translation failed: {doc_id}/{section_file}[{para_idx}] → {target_lang}: {e}")
            translated = None
        if translated is None:
            return text  # fallback: the original text, not cached
        translated = self._sanitize_translated_paragraph(translated.strip(), text)
        self._save_translation(
            doc_id, section_file, para_idx,
            self.source_language, target_lang, text, source_hash, translated
        )
        logger.info(
            f"Translated: {doc_id}/{section_file}[{para_idx}] → {target_lang} "
            f"({len(text)}→{len(translated)} characters)"
        )
        return translated
    
    @staticmethod
    def _sanitize_translated_paragraph(translated: str, source: str) -> str:
        """
        Removes unwanted Markdown formatting from translated paragraphs.
        
        LLMs sometimes add heading markers (#) although the source text was a
        normal paragraph; Word would render it as a heading taking half a page.
        """
        # remove heading markers if the source text was no heading
        if not source.strip().startswith('#') and translated.strip().startswith('#'):
            cleaned = translated.strip().lstrip('#').strip()
            logger.debug(
                f"Heading markers removed from a paragraph translation: "
                f"'{translated[:50]}' → '{cleaned[:50]}'"
            )
            translated = cleaned
        
        return translated
    
    def _translate_table(
        self, table_block: str, target_lang: str,
        doc_id: str, section_file: str, para_idx: int
    ) -> str:
        """
        Translates a Markdown table as a whole.
        
        Strategy: the whole table goes to the LLM as one block, with the
        instruction to keep the pipe structure exactly. The LLM sees the
        context of all cells and translates more consistently.
        Then the structure is validated: same number of rows and columns.
        """
        
        # check the cache
        source_hash = self._text_hash(table_block)
        cached = self._get_cached_translation(
            doc_id, section_file, para_idx, target_lang, source_hash
        )
        if cached:
            logger.debug(f"Cache hit table: {doc_id}/{section_file}[{para_idx}] → {target_lang}")
            return cached
        
        # count the original structure for the validation
        orig_lines = [l for l in table_block.split('\n') if l.strip()]
        orig_row_count = len(orig_lines)
        orig_col_counts = []
        for line in orig_lines:
            if line.strip().startswith('|'):
                orig_col_counts.append(line.count('|'))
        
        # The whole table in one text, so all cells are translated in context
        try:
            translated = self._translate_via_service(table_block, target_lang, "doc_table")
            if translated is None:
                return table_block
            translated = translated.strip()
            
            # remove a Markdown code block wrapper if the LLM added one
            if translated.startswith('```'):
                lines = translated.split('\n')
                # remove the first and the last line with ```
                if lines[-1].strip() == '```':
                    lines = lines[1:-1]
                elif lines[0].startswith('```'):
                    lines = lines[1:]
                translated = '\n'.join(lines)
            
            # Strukturvalidierung
            trans_lines = [l for l in translated.split('\n') if l.strip()]
            trans_row_count = len(trans_lines)
            trans_col_counts = []
            for line in trans_lines:
                if line.strip().startswith('|'):
                    trans_col_counts.append(line.count('|'))
            
            # row check
            if trans_row_count != orig_row_count:
                logger.warning(
                    f"Table translation: number of rows changed "
                    f"({orig_row_count} → {trans_row_count}) in "
                    f"{doc_id}/{section_file}[{para_idx}] → using the original"
                )
                return table_block
            
            # column check (same number of pipes in every row?)
            if orig_col_counts and trans_col_counts and orig_col_counts != trans_col_counts:
                logger.warning(
                    f"Table translation: column structure changed in "
                    f"{doc_id}/{section_file}[{para_idx}] → using the original"
                )
                return table_block
            
            # length check (protection against hallucinations)
            if len(translated) > len(table_block) * 2.5:
                logger.warning(
                    f"Table translation suspiciously long "
                    f"({len(translated)} vs {len(table_block)}) in "
                    f"{doc_id}/{section_file}[{para_idx}] → using the original"
                )
                return table_block
            
            # store in the cache
            self._save_translation(
                doc_id, section_file, para_idx,
                self.source_language, target_lang, table_block, source_hash, translated
            )
            
            logger.info(
                f"Table translated: {doc_id}/{section_file}[{para_idx}] → {target_lang} "
                f"({orig_row_count} rows)"
            )
            return translated
            
        except Exception as e:
            logger.error(
                f"Table translation failed: {doc_id}/{section_file}[{para_idx}] "
                f"→ {target_lang}: {e}"
            )
            return table_block  # fallback: the original
    
    def _translate_heading(
        self, heading_text: str, target_lang: str,
        doc_id: str, section_file: str, para_idx: int
    ) -> str:
        """
        Translates a Markdown heading title (without the # markers).
        
        Uses a strict prompt, so ONLY the short title is translated and the LLM
        adds no running text. The result is limited to the first line and to at
        most 3x the original length.
        """
        if not heading_text.strip():
            return heading_text
        
        # check the cache (same hash mechanism as _translate_paragraph)
        source_hash = self._text_hash(heading_text)
        cached = self._get_cached_translation(
            doc_id, section_file, para_idx, target_lang, source_hash
        )
        if cached:
            # validate cached translations as well (in case an old error was cached)
            first_line = cached.split('\n')[0].strip()
            first_line = first_line.lstrip('#').strip()
            if len(first_line) <= len(heading_text) * 3 + 20:
                return first_line
            else:
                logger.warning(
                    f"Cached heading translation too long ({len(cached)} characters), "
                    f"translating again: '{heading_text}'"
                )
        
        # cross-document cache for shared sections (e.g. the background.md heading)
        shared_files = {fname for _, fname in self.config.get('shared_sections', {}).items()}
        if not cached and section_file in shared_files:
            cross_cached = self._get_cached_translation_any_doc(
                section_file, para_idx, target_lang, source_hash
            )
            if cross_cached:
                first_line = cross_cached.split('\n')[0].strip().lstrip('#').strip()
                if len(first_line) <= len(heading_text) * 3 + 20:
                    self._save_translation(
                        doc_id, section_file, para_idx,
                        self.source_language, target_lang, heading_text, source_hash, first_line
                    )
                    logger.info(
                        f"Cross-document heading reuse: '{heading_text}' → '{first_line}' "
                        f"({target_lang})"
                    )
                    return first_line
        
        # Translation as a heading (kind doc_heading: the title only)
        try:
            translated = self._translate_via_service(heading_text, target_lang, "doc_heading")
            if translated is None:
                return heading_text
            translated = translated.strip()
            
            # safety: only the first line, remove Markdown markers
            translated = translated.split('\n')[0].strip()
            translated = translated.lstrip('#').strip()
            translated = translated.strip('"').strip("'")
            
            # translation suspiciously long -> only the first sentence fragment
            if len(translated) > len(heading_text) * 3 + 20:
                logger.warning(
                    f"Heading translation too long: '{translated[:80]}...' "
                    f"(original: '{heading_text}')"
                )
                # try to cut at the first colon or full stop
                for sep in [':', '.', '—', '–', ',']:
                    if sep in translated[:60]:
                        translated = translated[:translated.index(sep)].strip()
                        break
            
            self._save_translation(
                doc_id, section_file, para_idx,
                self.source_language, target_lang, heading_text, source_hash, translated
            )
            
            logger.info(f"Heading translated: '{heading_text}' → '{translated}' ({target_lang})")
            return translated
            
        except Exception as e:
            logger.error(f"Heading translation failed: {e}")
            return heading_text
    
    @staticmethod
    def _paragraphs(content: str) -> list[str]:
        """
        The paragraphs of a Markdown text (blocks between empty lines). A heading line
        outside code blocks is always a paragraph of its own, also when text follows it
        without an empty line (otherwise the text would be translated as part of the heading).
        """
        lines, in_code = [], False
        for line in content.split('\n'):
            if line.strip().startswith('```'):
                in_code = not in_code
            if not in_code and line.lstrip().startswith('#'):
                lines += ['', line, '']
            else:
                lines.append(line)
        return [p.strip('\n') for p in '\n'.join(lines).split('\n\n') if p.strip()]

    def _translate_sections(
        self, sections: list[tuple[str, str]], doc_id: str, target_lang: str,
        progress_callback: Callable = None
    ) -> list[tuple[str, str]]:
        """
        Translates all sections paragraph by paragraph.
        
        Code blocks (``` ... ```) and Mermaid blocks are NOT translated, as the
        Mermaid labels are handled separately through config/diagram_labels.yaml.
        """
        translated_sections = []
        total_paras = sum(len(self._paragraphs(content)) for _, content in sections)
        current_para = 0
        
        for filename, content in sections:
            paragraphs = self._paragraphs(content)
            translated_paras = []
            in_code_block = False
            
            for para_idx, para in enumerate(paragraphs):
                stripped = para.strip()
                
                # detect code blocks (may span several paragraphs):
                # count the ``` markers in the paragraph
                fence_count = stripped.count('```')
                if fence_count > 0:
                    if fence_count % 2 == 0:
                        # the block starts and ends in the same paragraph -> do not translate
                        translated_paras.append(stripped)
                        current_para += 1
                        if progress_callback:
                            progress_callback(current_para / max(total_paras, 1))
                        continue
                    else:
                        # odd: start or end of a block
                        in_code_block = not in_code_block
                        translated_paras.append(stripped)
                        current_para += 1
                        if progress_callback:
                            progress_callback(current_para / max(total_paras, 1))
                        continue
                
                # inside a code block: do not translate
                if in_code_block:
                    translated_paras.append(stripped)
                    current_para += 1
                    if progress_callback:
                        progress_callback(current_para / max(total_paras, 1))
                    continue
                
                # headings: keep #, translate ONLY the title
                if stripped.startswith('#'):
                    hashes = ''
                    rest = stripped
                    while rest.startswith('#'):
                        hashes += '#'
                        rest = rest[1:]
                    heading_text = rest.strip()
                    
                    translated_heading = self._translate_heading(
                        heading_text, target_lang, doc_id, filename, para_idx
                    )
                    translated_paras.append(f"{hashes} {translated_heading}")
                
                # keep separators (with and without spaces)
                elif stripped.replace(' ', '') in ('---', '***', '___'):
                    translated_paras.append(stripped)
                
                # tables: translate the cell content, keep the structure (|)
                elif stripped.startswith('|') and '|' in stripped[1:]:
                    translated_table = self._translate_table(
                        stripped, target_lang, doc_id, filename, para_idx
                    )
                    translated_paras.append(translated_table)
                
                # very short paragraphs (<=5 characters): do not translate (artefacts)
                elif len(stripped) <= 5:
                    translated_paras.append(stripped)
                
                # bold labels (e.g. **Example:** or **System prompt:**):
                # translated with the heading translator (strict, short, safe)
                elif re.match(r'^\*\*[^*]+\*\*\s*$', stripped):
                    inner = re.sub(r'^\*\*(.+?)\*\*\s*$', r'\1', stripped).strip()
                    has_colon = inner.endswith(':')
                    label = inner.rstrip(':').strip()
                    if label:
                        translated_label = self._translate_heading(
                            label, target_lang, doc_id, filename, para_idx
                        )
                        colon = ':' if has_colon else ''
                        translated_paras.append(f"**{translated_label}{colon}**")
                    else:
                        translated_paras.append(stripped)
                
                # normal text
                else:
                    translated = self._translate_paragraph(
                        stripped, target_lang, doc_id, filename, para_idx
                    )
                    
                    # length guard: the translation should not be more than 3x as long as
                    # the source text (protection against hallucinations)
                    if len(translated) > len(stripped) * 3 and len(stripped) > 20:
                        logger.warning(
                            f"Translation suspiciously long ({len(translated)} vs {len(stripped)} characters) "
                            f"for {doc_id}/{filename}[{para_idx}] → using the original"
                        )
                        translated_paras.append(stripped)
                    else:
                        translated_paras.append(translated)
                
                current_para += 1
                if progress_callback:
                    progress_callback(current_para / max(total_paras, 1))
            
            translated_sections.append((filename, '\n\n'.join(translated_paras)))
        
        return translated_sections
    
    # =========================================================================
    # EXTENDING AN EXISTING DOCX
    # =========================================================================
    
    @staticmethod
    def _existing_docx_pattern(doc_config: dict, lang: str) -> str:
        """existing_docx of a language: a pattern with {lang}, or one entry per language."""
        value = doc_config.get('existing_docx')
        if isinstance(value, str):
            return value.replace('{lang}', lang)
        return (value or {}).get(lang, '')

    def _resolve_existing_docx(self, path_pattern: str) -> Optional[Path]:
        """
        Resolves an existing_docx path.
        
        A relative path is relative to the output directory (SURVEY_OUTPUT_DIR), where
        the pipeline writes its reports. Supports glob patterns (e.g.
        'full_pipeline_*/survey_report_*_de.docx'); with several matches the newest
        file (by mtime) is chosen.
        """
        p = Path(path_pattern)
        if not p.is_absolute():
            p = _output_dir() / p
        
        # direct path without glob
        if '*' not in str(p) and '?' not in str(p):
            return p if p.exists() else None
        
        # glob pattern: resolve from the root
        # find the first path part with a wildcard
        parts = p.parts
        base = Path(parts[0])
        glob_pattern = ''
        for i, part in enumerate(parts[1:], start=1):
            if '*' in part or '?' in part:
                glob_pattern = str(Path(*parts[i:]))
                break
            base = base / part
        
        if not glob_pattern:
            return None
        
        matches = sorted(base.glob(glob_pattern), key=lambda f: f.stat().st_mtime, reverse=True)
        # only reports of runs of the active survey (several surveys share the output directory);
        # a file whose run names no survey is kept
        active = _active_survey_id()
        if active:
            matches = [m for m in matches if _run_survey_id(m.parent) in (None, active)]
        
        if matches:
            logger.info(f"Glob '{path_pattern}' → {matches[0]} ({len(matches)} matches)")
            return matches[0]
        
        logger.warning(f"No files found for the pattern: {path_pattern}")
        return None
    
    def _strip_cover_and_toc(self, doc: Document) -> Document:
        """
        Removes the title page, table of contents and background from an
        existing report.
        
        Detects the end of the prefix at the first heading starting with
        'Teil' / 'Part' / 'Partie', or at the third pageBreak.
        
        Headings such as 'Inhaltsverzeichnis', 'Hintergrund', 'Background' etc.
        are recognised explicitly as parts of the prefix and removed.
        """
        body = doc.element.body
        elements_to_remove = []
        found_content_start = False
        page_break_count = 0
        
        # headings of the prefix that are removed
        prefix_headings = (
            'Inhaltsverzeichnis', 'Table of Contents', 'Table des matières', 'Indice', 'Sommario',
            'Hintergrund', 'Background', 'Contexte', 'Contesto',
            'Einführung', 'Introduction', 'Introduzione',
        )
        
        for element in list(body):
            if found_content_start:
                break
            
            tag = element.tag.split('}')[-1] if '}' in element.tag else element.tag
            
            if tag == 'p':
                # check for a page break
                ppr = element.find(qn('w:r'))
                if ppr is not None:
                    for br in element.findall(f'.//{qn("w:br")}'):
                        if br.get(qn('w:type')) == 'page':
                            page_break_count += 1
                
                # check for lastRenderedPageBreak (also indicates a page break)
                for last_br in element.findall(f'.//{qn("w:lastRenderedPageBreak")}'):
                    page_break_count += 1
            
            elif tag == 'sectPr':
                # do not remove the section properties
                continue
            
            # is this a heading?
            if tag == 'p':
                pPr = element.find(qn('w:pPr'))
                if pPr is not None:
                    pStyle = pPr.find(qn('w:pStyle'))
                    if pStyle is not None:
                        style_val = pStyle.get(qn('w:val'), '')
                        # detect heading styles AND the TOC heading
                        is_heading = (
                            style_val.startswith('Heading')
                            or style_val == 'TOCHeading'
                        )
                        if is_heading:
                            heading_text = ''.join(
                                t.text or '' for t in element.findall(f'.//{qn("w:t")}')
                            ).strip()
                            
                            # the first level-1 heading that is no prefix heading starts the
                            # content ("Part I", "Teil I", "Parte I", ... in any language)
                            if style_val == 'Heading1' and not any(heading_text.startswith(ph) for ph in prefix_headings):
                                found_content_start = True
                                continue  # keep this element
                            
                            # prefix headings -> remove and continue
                            if any(heading_text.startswith(ph) for ph in prefix_headings):
                                elements_to_remove.append(element)
                                logger.debug(f"Prefix heading removed: '{heading_text}'")
                                continue
            
            # after 3 page breaks + the next heading = start of the real content
            # (3 rather than 2: cover break, TOC break, background break)
            if page_break_count >= 3 and tag == 'p':
                pPr = element.find(qn('w:pPr'))
                if pPr is not None:
                    pStyle = pPr.find(qn('w:pStyle'))
                    if pStyle is not None:
                        style_val = pStyle.get(qn('w:val'), '')
                        if style_val.startswith('Heading') or style_val == 'TOCHeading':
                            found_content_start = True
                            continue
            
            if not found_content_start:
                elements_to_remove.append(element)
        
        # remove the elements (only if the start of the content was found)
        if not found_content_start:
            logger.warning(
                "Could not find the start of the content (a level-1 heading after the cover and the "
                "table of contents). Cover/TOC/background are NOT removed, to avoid losing data."
            )
            return doc
        
        for element in elements_to_remove:
            body.remove(element)
        
        logger.info(f"Prefix removed: {len(elements_to_remove)} elements "
                    f"({page_break_count} page breaks found)")
        
        return doc
    
    def _prepend_to_existing_docx(
        self, docx_path: Path, doc_config: dict, lang: str, doc_id: str = 'unknown'
    ) -> Optional[Document]:
        """
        Puts configurable sections before and/or after an existing .docx.
        
        Reads 'prefix_sections' and 'suffix_sections' from the document configuration.
        The Markdown content is translated with the LLM for the other languages.
        
        Default (without an explicit configuration):
            prefix: cover + introduction + TOC
            suffix: (empty)
        """
        if not docx_path.exists():
            logger.warning(f"Existing DOCX not found: {docx_path}")
            return None
        
        try:
            from docxcompose.composer import Composer
        except ImportError:
            logger.warning(
                "docxcompose not installed (pip install docxcompose). "
                "Adding only the footer to the existing file."
            )
            doc = Document(docx_path)
            self._add_page_footer(doc, lang, doc_config)
            return doc
        
        # prefix sections from the configuration (default: cover + introduction)
        prefix_refs = doc_config.get('prefix_sections', [
            'shared:cover', 'shared:introduction'
        ])
        suffix_refs = doc_config.get('suffix_sections', [])
        
        # resolve the sections (load the Markdown)
        prefix_sections_source = self._resolve_section_refs(prefix_refs)
        suffix_sections_source = self._resolve_section_refs(suffix_refs)
        
        # translate into the other languages
        if lang != self.source_language:
            prefix_sections = self._prepare_sections_for_lang(
                prefix_sections_source, doc_id, lang
            )
            suffix_sections = self._prepare_sections_for_lang(
                suffix_sections_source, doc_id, lang
            )
        else:
            prefix_sections = prefix_sections_source
            suffix_sections = suffix_sections_source
        
        # resolve the cover per language (replace the placeholders)
        prefix_sections = [
            ('cover.md', self._resolve_cover(doc_config, lang))
            if fname == 'cover.md' else (fname, content)
            for fname, content in prefix_sections
        ]
        
        # build the prefix document (cover + sections + TOC)
        prefix_doc = self._build_from_sections(
            prefix_sections, lang, include_toc=True, doc_config=doc_config
        )
        
        # existing file: remove the cover + TOC (replaced by the prefix)
        existing_doc = Document(docx_path)
        self._strip_cover_and_toc(existing_doc)
        
        # merge
        composer = Composer(prefix_doc)
        composer.append(existing_doc)
        
        # append the suffix (e.g. the background at the end)
        if suffix_sections:
            suffix_doc = self._build_from_sections(
                suffix_sections, lang, include_toc=False
            )
            # make sure there is a page break before the suffix
            if suffix_doc.paragraphs:
                suffix_doc.paragraphs[0].paragraph_format.page_break_before = True
            composer.append(suffix_doc)
        
        return composer.doc
    
    def _resolve_section_refs(
        self, section_refs: list[str]
    ) -> list[tuple[str, str]]:
        """
        Resolves a list of section references into (filename, content) pairs.
        
        Supports:
            - 'shared:cover'       -> shared_sections['cover']
            - 'shared:background'  -> shared_sections['background']
            - 'introduction.md'    -> direct file reference
        """
        sections = []
        for ref in section_refs:
            if ref.startswith('shared:'):
                key = ref.split(':')[1]
                filename = self.config['shared_sections'].get(key, '')
                if not filename:
                    logger.warning(f"Shared section '{key}' not defined in the configuration")
                    continue
            else:
                filename = ref
            
            path = self.source_dir / filename
            if path.exists():
                sections.append((filename, path.read_text(encoding='utf-8')))
            else:
                logger.warning(f"Section file not found: {path}")
        
        return sections
    
    def _prepare_sections_for_lang(
        self, sections_source: list[tuple[str, str]], doc_id: str, lang: str
    ) -> list[tuple[str, str]]:
        """
        Prepares sections for a target language.
        
        The cover is NOT translated here (it has language-specific placeholders).
        All other sections are translated with the LLM.
        """
        sections_to_translate = [
            (fname, content) for fname, content in sections_source
            if fname != 'cover.md'
        ]
        cover_sections = [
            (fname, content) for fname, content in sections_source
            if fname == 'cover.md'
        ]
        
        translated = self._translate_sections(
            sections_to_translate, doc_id, lang
        ) if sections_to_translate else []
        
        # keep the order: cover placeholder + translated sections
        result = []
        trans_idx = 0
        for fname, content in sections_source:
            if fname == 'cover.md':
                result.append((fname, content))  # replaced later by _resolve_cover
            else:
                if trans_idx < len(translated):
                    result.append(translated[trans_idx])
                    trans_idx += 1
        
        return result
    
    # =========================================================================
    # MAIN FUNCTIONS
    # =========================================================================
    
    def build_document(
        self, doc_id: str, lang: str = 'de',
        output_dir: str = "output",
        progress_callback: Callable = None
    ) -> Optional[Path]:
        """
        Builds a single document in one language.
        
        Args:
            doc_id: key in documents.yaml (e.g. 'results_report')
            lang: target language
            output_dir: output directory
            progress_callback: fn(float) for the progress 0-1
            
        Returns:
            path of the created .docx file, or None on errors
        """
        doc_config = self.config['documents'].get(doc_id)
        if not doc_config:
            logger.error(f"Document '{doc_id}' not found in the configuration")
            return None
        
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        lang_suffix = lang.upper()
        filename = f"{doc_config['filename']}_{lang_suffix}.docx"
        filepath = output_path / filename
        
        # === case A: extend an existing DOCX ===
        if 'existing_docx' in doc_config:
            path_pattern = self._existing_docx_pattern(doc_config, lang)
            existing_path = self._resolve_existing_docx(path_pattern)
            if not existing_path:
                logger.error(
                    f"Analysis document not found: {path_pattern}\n"
                    f"  Run the analysis first (pipeline/export)."
                )
                return None
            doc = self._prepend_to_existing_docx(existing_path, doc_config, lang, doc_id)
            if doc:
                doc.save(filepath)
                logger.info(f"Document extended: {filepath}")
                return filepath
            return None
        
        # === case B: assemble from .md files ===
        sections_source = self._resolve_sections(doc_config, self.source_language)
        
        if not sections_source:
            logger.warning(f"No sections found for {doc_id}")
            return None
        
        if lang == self.source_language:
            doc = self._build_from_sections(sections_source, lang, doc_config=doc_config)
        else:
            # create the cover in the target language, translate the rest
            # (heading_offset is passed through the translation)
            sections_to_translate = []
            cover_section = None
            offsets = {}  # filename → heading_offset
            
            for fname, content, offset in sections_source:
                offsets[fname] = offset
                if fname == 'cover.md':
                    cover_section = ('cover.md', self._resolve_cover(doc_config, lang), 0)
                else:
                    sections_to_translate.append((fname, content))
            
            translated = self._translate_sections(
                sections_to_translate, doc_id, lang, progress_callback
            )
            
            # insert the cover at the front, restore the offset from the original sections
            all_sections = []
            if cover_section:
                all_sections.append(cover_section)
            for fname, content in translated:
                all_sections.append((fname, content, offsets.get(fname, 0)))
            
            doc = self._build_from_sections(all_sections, lang, doc_config=doc_config)
        
        doc.save(filepath)
        logger.info(f"Document created: {filepath}")
        return filepath
    
    def build_all(
        self, languages: list[str] = None, output_dir: str = "output",
        progress_callback: Callable = None
    ) -> dict[str, list[Path]]:
        """
        Builds all documents in all languages.
        
        Returns:
            dict {doc_id: [path per language]}
        """
        logger.info(f"Build started: DB={self.db_path.resolve()}, source_dir={self.source_dir}")
        if languages is None:
            languages = [self.source_language]
        
        results = {}
        doc_ids = list(self.config['documents'].keys())
        total = len(doc_ids) * len(languages)
        current = 0
        
        for doc_id in doc_ids:
            results[doc_id] = []
            
            for lang in languages:
                doc_name = self.config['documents'][doc_id]['filename']
                if progress_callback:
                    progress_callback(
                        current / total,
                        f"{doc_name} ({lang.upper()})..."
                    )
                
                path = self.build_document(doc_id, lang, output_dir)
                if path:
                    results[doc_id].append(path)
                
                current += 1
        
        return results
    
    def build_update(
        self, languages: list[str] = None, output_dir: str = "output",
        progress_callback: Callable = None
    ) -> dict[str, list[Path]]:
        """
        Builds only the documents that do not exist yet or whose source files
        have changed.
        
        Returns:
            dict {doc_id: [path per language]}
        """
        if languages is None:
            languages = [self.source_language]
        
        results = {}
        doc_ids = list(self.config['documents'].keys())
        total = len(doc_ids) * len(languages)
        current = 0
        skipped = 0
        
        for doc_id in doc_ids:
            results[doc_id] = []
            doc_config = self.config['documents'][doc_id]
            
            for lang in languages:
                doc_name = doc_config['filename']
                lang_suffix = lang.upper()
                filepath = Path(output_dir) / f"{doc_name}_{lang_suffix}.docx"
                
                if progress_callback:
                    progress_callback(
                        current / total,
                        f"Checking {doc_name} ({lang.upper()})..."
                    )
                
                # does the file exist and is it current?
                if filepath.exists() and not self._needs_rebuild(doc_id, lang, filepath):
                    logger.info(f"Skipping (up to date): {filepath}")
                    results[doc_id].append(filepath)
                    skipped += 1
                else:
                    path = self.build_document(
                        doc_id, lang, output_dir, progress_callback
                    )
                    if path:
                        results[doc_id].append(path)
                
                current += 1
        
        if skipped > 0:
            logger.info(f"Update: {skipped} documents skipped (up to date)")
        
        return results
    
    def _needs_rebuild(self, doc_id: str, lang: str, filepath: Path) -> bool:
        """
        Checks whether a document has to be rebuilt.
        
        True if:
        - documents.yaml is newer than the document
        - source files (.md or .docx) are newer than the document
        - translations in the cache are outdated (hash changed)
        """
        doc_config = self.config['documents'].get(doc_id, {})
        doc_mtime = filepath.stat().st_mtime
        
        # configuration newer than the document? (checked FIRST, so changes to
        # documents.yaml always trigger a rebuild)
        if self.config_path.exists() and self.config_path.stat().st_mtime > doc_mtime:
            logger.info(f"Rebuild needed: configuration newer than {filepath.name}")
            return True
        
        # existing_docx: is the source .docx newer?
        if 'existing_docx' in doc_config:
            path_pattern = self._existing_docx_pattern(doc_config, lang)
            src = self._resolve_existing_docx(path_pattern)
            if src and src.exists() and src.stat().st_mtime > doc_mtime:
                logger.info(f"Rebuild needed: source {src.name} newer than {filepath.name}")
                return True
            
            # also check prefix/suffix_sections and shared_sections, as they are put
            # before/after the existing_docx
            all_refs = (
                doc_config.get('prefix_sections', []) +
                doc_config.get('suffix_sections', [])
            )
            # without explicit prefix/suffix: check shared_sections
            if not all_refs:
                all_refs = [f"shared:{k}" for k in self.config.get('shared_sections', {})]
            
            for ref in all_refs:
                if ref.startswith('shared:'):
                    key = ref.split(':')[1]
                    filename = self.config['shared_sections'].get(key, '')
                else:
                    filename = ref
                if filename:
                    ref_path = self.source_dir / filename
                    if ref_path.exists() and ref_path.stat().st_mtime > doc_mtime:
                        logger.info(
                            f"Rebuild needed: {filename} newer than {filepath.name}"
                        )
                        return True
            
            return False
        
        # .md-based documents: are the source files newer?
        # (a group {'group': {..., 'sections': [...]}} contributes its sub-sections)
        refs = []
        for section_ref in doc_config.get('sections', []):
            if isinstance(section_ref, dict) and 'group' in section_ref:
                refs += [r for r in (section_ref['group'].get('sections') or []) if isinstance(r, str)]
            elif isinstance(section_ref, str):
                refs.append(section_ref)
        for section_ref in refs:
            if section_ref.startswith('shared:'):
                key = section_ref.split(':')[1]
                filename = self.config['shared_sections'].get(key, '')
            else:
                filename = section_ref
            
            src_path = self.source_dir / filename
            if src_path.exists() and src_path.stat().st_mtime > doc_mtime:
                return True
        
        return False
    
    # =========================================================================
    # CACHE MANAGEMENT
    # =========================================================================
    
    def get_translation_stats(self) -> dict:
        """Statistics of the translation cache."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT document_id, target_lang, COUNT(*) as cnt,
                       MIN(updated_at) as oldest, MAX(updated_at) as newest
                FROM document_translations
                GROUP BY document_id, target_lang
                ORDER BY document_id, target_lang
            """)
            stats = {}
            for doc_id, lang, count, oldest, newest in cursor.fetchall():
                if doc_id not in stats:
                    stats[doc_id] = {}
                stats[doc_id][lang] = {'count': count, 'oldest': oldest, 'newest': newest}
            conn.close()
            return stats
        except Exception:
            return {}
    
    def get_stale_translations(self) -> dict:
        """
        Finds translations whose source text has changed.
        
        Returns:
            {doc_id: {lang: count_stale}}
        """
        stale = {}
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT document_id, section_file, paragraph_index,
                       target_lang, source_hash, source_text
                FROM document_translations
            """)
            for doc_id, section_file, para_idx, lang, old_hash, source_text in cursor.fetchall():
                current_hash = self._text_hash(source_text)
                # does the source file have the same text?
                src_path = self.source_dir / section_file
                if src_path.exists():
                    content = src_path.read_text(encoding='utf-8')
                    paras = [p.strip() for p in self._paragraphs(content)]
                    if para_idx < len(paras):
                        actual_hash = self._text_hash(paras[para_idx])
                        if actual_hash != old_hash:
                            stale.setdefault(doc_id, {})
                            stale[doc_id][lang] = stale[doc_id].get(lang, 0) + 1
            conn.close()
        except Exception as e:
            logger.warning(f"Stale check failed: {e}")
        return stale
    
    def clear_translations(self, doc_id: str = None, target_lang: str = None) -> int:
        """
        Deletes translations from the cache.
        
        Args:
            doc_id: only for this document (None = all)
            target_lang: only for this language (None = all)
        """
        try:
            db_abs = self.db_path.resolve()
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # count before deleting
            cursor.execute("SELECT COUNT(*) FROM document_translations")
            count_before = cursor.fetchone()[0]
            logger.info(f"Clearing the cache: DB={db_abs}, entries before={count_before}")
            
            if doc_id and target_lang:
                cursor.execute(
                    "DELETE FROM document_translations WHERE document_id = ? AND target_lang = ?",
                    (doc_id, target_lang)
                )
            elif doc_id:
                cursor.execute(
                    "DELETE FROM document_translations WHERE document_id = ?",
                    (doc_id,)
                )
            elif target_lang:
                cursor.execute(
                    "DELETE FROM document_translations WHERE target_lang = ?",
                    (target_lang,)
                )
            else:
                cursor.execute("DELETE FROM document_translations")
            
            deleted = cursor.rowcount
            conn.commit()
            conn.close()
            logger.info(f"Translation cache: {deleted} entries deleted (DB: {db_abs})")
            return deleted
        except Exception as e:
            logger.error(f"Clearing the cache failed: {e}")
            return 0
    
    def get_document_list(self) -> list[dict]:
        """A list of all configured documents (for the interface)."""
        docs = []
        for doc_id, doc_config in self.config.get('documents', {}).items():
            docs.append({
                'id': doc_id,
                'filename': doc_config['filename'],
                'title': doc_config['subtitle'].get('de', doc_id),
                'is_existing': 'existing_docx' in doc_config,
                'section_count': len(doc_config.get('sections', []))
            })
        return docs
