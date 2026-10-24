# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Word export module of the survey analysis tool: creates multilingual Word
documents with python-docx.
"""

import logging
import re
from pathlib import Path
from typing import Optional
from io import BytesIO

from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

import matplotlib.pyplot as plt

from .translation import get_translation_manager
from .charts import ChartGenerator
from .languages import text


def _fixed_text(texts: dict, lang: str):
    """
    A fixed text from a literal {de, fr, en} dict in this module: the
    language, else English (as the catalogs in locales/ do), never German.
    These literals belong in locales/ as well.
    """
    return texts.get(lang) or texts.get('en') or next(iter(texts.values()))


def _texts(key: str, **fmt) -> dict:
    """
    {lang: text} of a catalog entry in locales/ for every catalog language
    (strings formatted with ``fmt``; lists and mappings as they are).
    Missing in a language: the English entry.
    """
    from .languages import _catalog, available_catalogs
    def node_of(code):
        node = _catalog(code)
        for part in key.split('.'):
            node = node.get(part) if isinstance(node, dict) else None
        return node
    out = {}
    for code in available_catalogs():
        node = node_of(code)
        if node is None:
            node = node_of('en')
        out[code] = node.format(**fmt) if isinstance(node, str) and fmt else node
    return out


def _word(key: str, **fmt) -> dict:
    """Fixed texts of the Word export (locales/*.yaml, section word)."""
    return _texts(f"word.{key}", **fmt)


def _config_text(value, lang: str, default=''):
    """A multilingual configuration text in ``lang``, falling back to the survey's source language."""
    from .languages import localized
    from .prompts import source_language
    if isinstance(value, dict):
        return localized(value, lang, source_language(), default=default)
    return default if value in (None, '') else value

logger = logging.getLogger(__name__)
from utils.languages import text as _text


def clean_question_title(question_text: str) -> str:
    """
    Removes filling instructions from question texts, for clean headings.
    Handles German, French and English.
    
    Examples:
    - "Please sort...(Ranking - please order...)" -> "Please sort..."
    - "AI tools Which do you use...(several answers possible)" -> "Which AI tools do you use?"
    - "Which trends...\nName the most important..." -> "Which trends...?"
    
    Args:
        question_text: full question text
        
    Returns:
        cleaned question text without filling instructions
    """
    if not question_text:
        return question_text
    
    text = question_text.strip()
    
    # patterns of filling instructions in brackets at the end (DE/FR/EN)
    instruction_patterns = [
        # German
        r'\s*\(Mehrfachauswahl[^)]*\)\s*$',
        r'\s*\(Einfachauswahl[^)]*\)\s*$',
        r'\s*\(Ranking[^)]*\)\s*$',
        r'\s*\(max\.\s*\d+[^)]*\)\s*$',
        r'\s*\(bitte[^)]*\)\s*$',
        r'\s*\(Freitext[^)]*\)\s*$',
        # French
        r'\s*\(Choix multiple[^)]*\)\s*$',
        r'\s*\(Choix unique[^)]*\)\s*$',
        r'\s*\(Classement[^)]*\)\s*$',
        r'\s*\(max\.\s*\d+[^)]*\)\s*$',
        r'\s*\(veuillez[^)]*\)\s*$',
        r'\s*\(Texte libre[^)]*\)\s*$',
        # English
        r'\s*\(Multiple choice[^)]*\)\s*$',
        r'\s*\(Single choice[^)]*\)\s*$',
        r'\s*\(Ranking[^)]*\)\s*$',
        r'\s*\(max\.\s*\d+[^)]*\)\s*$',
        r'\s*\(please[^)]*\)\s*$',
        r'\s*\(Free text[^)]*\)\s*$',
    ]
    
    for pattern in instruction_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # remove instructions after a line break
    # e.g. "Question?\nName the most important..." -> "Question?"
    if '\n' in text:
        lines = text.split('\n')
        # keep only the first line if it is a question
        first_line = lines[0].strip()
        if first_line.endswith('?') or first_line.endswith('.'):
            text = first_line
        else:
            # check whether a later line is the actual question
            for line in lines:
                line = line.strip()
                if line.endswith('?'):
                    text = line
                    break
    
    # remove double spaces
    text = re.sub(r'\s+', ' ', text).strip()
    
    # (no punctuation added: a configured title like "… (0–10)" or "… – Other"
    # used to get a stray "?")
    return text


def _add_field_fldchar(para, field_code: str, placeholder: str = '1'):
    """
    Inserts a Word field with the fldChar approach (begin/separate/end).
    
    More reliable than fldSimple, because Word keeps the RunProperties of
    every run when resolving the field. Prevents page numbers from being
    rendered in another font size than the surrounding text.
    """
    def _make_rpr():
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
    
    # Begin
    r_begin = OxmlElement('w:r')
    r_begin.append(_make_rpr())
    fld_begin = OxmlElement('w:fldChar')
    fld_begin.set(qn('w:fldCharType'), 'begin')
    r_begin.append(fld_begin)
    para._p.append(r_begin)
    
    # Instruction
    r_instr = OxmlElement('w:r')
    r_instr.append(_make_rpr())
    instr_text = OxmlElement('w:instrText')
    instr_text.set(qn('xml:space'), 'preserve')
    instr_text.text = field_code
    r_instr.append(instr_text)
    para._p.append(r_instr)
    
    # Separate
    r_sep = OxmlElement('w:r')
    r_sep.append(_make_rpr())
    fld_sep = OxmlElement('w:fldChar')
    fld_sep.set(qn('w:fldCharType'), 'separate')
    r_sep.append(fld_sep)
    para._p.append(r_sep)
    
    # result (placeholder)
    r_result = OxmlElement('w:r')
    r_result.append(_make_rpr())
    t = OxmlElement('w:t')
    t.text = placeholder
    r_result.append(t)
    para._p.append(r_result)
    
    # End
    r_end = OxmlElement('w:r')
    r_end.append(_make_rpr())
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    r_end.append(fld_end)
    para._p.append(r_end)


def add_markdown_to_document(doc: Document, markdown_text: str, lang: str = 'de'):
    """
    Converts Markdown text and adds it to the Word document.
    
    Supports:
    - # ## ### #### headings (levels 1-4)
    - **bold** / __bold__
    - *italic* / _italic_
    - `inline code`
    - - bullet lists and * bullet lists
    - 1. numbered lists
    - | table | header | ... |
    - ``` code blocks ```
    - ```mermaid diagrams -> PNG image (with a fallback to a code block)
    - --- / * * * / ___ horizontal rules
    - normal paragraphs
    
    Args:
        doc: Word document
        markdown_text: Markdown-formatted text
        lang: target language of the Mermaid diagram labels
    """
    if not markdown_text:
        return
    
    lines = markdown_text.strip().split('\n')
    current_list_items = []
    current_list_type = None  # 'bullet' or 'numbered'
    in_code_block = False
    in_mermaid_block = False
    code_block_lines = []
    current_table_rows = []
    
    def flush_list():
        nonlocal current_list_items, current_list_type
        if current_list_items:
            if current_list_type == 'numbered':
                _flush_numbered_list(doc, current_list_items)
            else:
                _flush_list_items(doc, current_list_items)
            current_list_items = []
            current_list_type = None
    
    def flush_table():
        nonlocal current_table_rows
        if current_table_rows:
            _add_table(doc, current_table_rows)
            current_table_rows = []
    
    for line in lines:
        line = line.rstrip()
        
        # === CODE-BLOCK / MERMAID-BLOCK ===
        if line.strip().startswith('```'):
            if in_code_block or in_mermaid_block:
                # end the block
                if in_mermaid_block:
                    _add_mermaid_diagram(doc, code_block_lines, lang)
                else:
                    _add_code_block(doc, code_block_lines)
                code_block_lines = []
                in_code_block = False
                in_mermaid_block = False
            else:
                # start a block; detect Mermaid
                flush_list()
                flush_table()
                block_lang = line.strip()[3:].strip().lower()
                if block_lang == 'mermaid':
                    in_mermaid_block = True
                else:
                    in_code_block = True
            continue
        
        if in_code_block or in_mermaid_block:
            code_block_lines.append(line)
            continue
        
        # === TABLE ===
        if line.strip().startswith('|') and line.strip().endswith('|'):
            flush_list()
            # detect a separator row (| --- | --- |)
            stripped_cells = [c.strip() for c in line.strip('|').split('|')]
            if all(re.match(r'^:?-+:?$', c.strip()) for c in stripped_cells if c.strip()):
                # skip the separator row (taken into account when rendering the table)
                continue
            current_table_rows.append(line)
            continue
        else:
            # end the table if we were in one
            flush_table()
        
        # === EMPTY LINE ===
        if not line:
            flush_list()
            continue
        
        # === HORIZONTAL RULE ===
        stripped = line.strip()
        if stripped in ('---', '***', '___', '* * *') or \
           re.match(r'^[-*_]\s*[-*_]\s*[-*_][\s*_-]*$', stripped):
            flush_list()
            # horizontal rule as a thin paragraph with a border
            _add_horizontal_rule(doc)
            continue
        
        # === HEADING ===
        heading_match = re.match(r'^(#{1,4})\s+(.+?)(?:\s*\\?\{.*\})?$', line)
        if not heading_match and line.startswith('#'):
            heading_match = re.match(r'^(#{1,4})\s*(.*)', line)
        if heading_match:
            flush_list()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            # escaped Markdown numbers: "1\. Text" → "1. Text"
            heading_text = heading_text.replace('\\. ', '. ').replace('\\)', ')')
            doc.add_heading(heading_text, level=min(level, 4))
            continue
        
        # === NUMBERED LIST ===
        num_match = re.match(r'^(\d+)\.\s+(.+)', line)
        if num_match:
            if current_list_type == 'bullet':
                flush_list()
            current_list_type = 'numbered'
            current_list_items.append(num_match.group(2))
            continue
        
        # === BULLET LIST ===
        if line.startswith('- ') or line.startswith('* '):
            if current_list_type == 'numbered':
                flush_list()
            current_list_type = 'bullet'
            current_list_items.append(line[2:])
            continue
        
        # === NORMAL PARAGRAPH ===
        flush_list()
        _add_formatted_paragraph(doc, line)
    
    # close the remaining elements
    flush_list()
    flush_table()
    if (in_code_block or in_mermaid_block) and code_block_lines:
        if in_mermaid_block:
            _add_mermaid_diagram(doc, code_block_lines, lang)
        else:
            _add_code_block(doc, code_block_lines)


def _add_code_block(doc: Document, lines: list):
    """Adds a code block as a formatted element."""
    if not lines:
        return
    
    code_text = '\n'.join(lines)
    
    para = doc.add_paragraph()
    para.style = doc.styles['Normal']
    
    # grey background through shading
    shading = OxmlElement('w:shd')
    shading.set(qn('w:val'), 'clear')
    shading.set(qn('w:color'), 'auto')
    shading.set(qn('w:fill'), 'F2F2F2')
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    
    # line by line as runs with line breaks
    for i, code_line in enumerate(lines):
        run = para.add_run(code_line)
        run.font.name = 'Consolas'
        run.font.size = Pt(9)
        run.font.color.rgb = RGBColor(51, 51, 51)
        if i < len(lines) - 1:
            run.add_break()
    
    # apply the shading to the paragraph
    pPr = para._p.get_or_add_pPr()
    pPr.append(shading)


def _add_mermaid_diagram(doc: Document, lines: list, lang: str = 'de'):
    """
    Renders a Mermaid code block as a PNG image in the Word document.
    
    Supports captions with `%% caption: text` in the Mermaid code.
    Sets keep_with_next, so the image and its caption stay together.
    
    Args:
        doc: Word document
        lines: Mermaid code lines (without the ``` markers)
        lang: target language for the label substitution
    """
    if not lines:
        return
    
    # extract the caption (and remove it from the lines to render)
    caption_text = None
    render_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('%% caption:'):
            caption_text = stripped[len('%% caption:'):].strip()
        else:
            render_lines.append(line)
    
    mermaid_code = '\n'.join(render_lines)
    
    # substitute the caption placeholders (before trying to render, so the
    # caption has resolved labels in the fallback path too)
    try:
        from .mermaid_renderer import MermaidRenderer
        renderer = MermaidRenderer(lang=lang)
        if caption_text:
            caption_text = renderer._substitute_labels(caption_text)
    except ImportError:
        renderer = None
        logger.info("MermaidRenderer not available, using a code block")
    except Exception as e:
        renderer = None
        logger.warning(f"MermaidRenderer init error: {e}")
    
    # try to render as PNG
    png_bytes = None
    if renderer is not None:
        try:
            png_bytes = renderer.render(mermaid_code)
        except Exception as e:
            logger.warning(f"Mermaid rendering error: {e}, fallback to a code block")
    
    if png_bytes:
        from io import BytesIO
        
        # image size for a sensible scaling
        image_stream = BytesIO(png_bytes)
        img_width_px, img_height_px = _get_png_dimensions(png_bytes)
        
        # A4 text area: about 6.3" wide, about 9" high (with margins)
        max_width_inches = 5.0
        max_height_inches = 4.5  # conservative: room for text above/below
        
        if img_width_px and img_height_px:
            aspect = img_height_px / img_width_px
            
            if aspect > 1.8:
                # very tall diagram (TD with many nodes): shrink strongly
                insert_width = min(max_height_inches / aspect, 3.0)
            elif aspect > 1.2:
                # tall diagram: choose the width so the height fits
                insert_width = min(max_height_inches / aspect, 3.5)
            elif aspect < 0.5:
                # very wide diagram (LR): full width
                insert_width = max_width_inches
            else:
                # normal ratio
                insert_width = min(4.0, max_width_inches)
            
            logger.debug(
                f"Mermaid image: {img_width_px}×{img_height_px}px, "
                f"aspect={aspect:.2f}, insert width={insert_width:.1f}\""
            )
        else:
            # fallback if the dimensions cannot be read
            insert_width = 4.5
        
        # image paragraph
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(8)
        para.paragraph_format.space_after = Pt(4)
        
        # keep_with_next: do not separate the image from its caption
        _set_keep_with_next(para)
        
        run = para.add_run()
        run.add_picture(image_stream, width=Inches(insert_width))
        
        # add the caption
        if caption_text:
            _add_mermaid_caption(doc, caption_text, lang)
        
        logger.info(f"Mermaid diagram inserted ({len(png_bytes)} bytes, {lang})")
        return
    
    # fallback: insert as a normal code block and keep the caption
    if renderer is not None:
        logger.warning("Mermaid rendering failed, fallback to a code block")
    _add_code_block(doc, render_lines)
    if caption_text:
        _add_mermaid_caption(doc, caption_text, lang)


def _set_keep_with_next(para):
    """Sets keep_with_next on a paragraph, so it is not separated from the next one."""
    pPr = para._p.get_or_add_pPr()
    keep = OxmlElement('w:keepNext')
    pPr.append(keep)


def _add_mermaid_caption(doc: Document, caption_text: str, lang: str = 'de'):
    """
    Adds a caption with automatic SEQ numbering.
    
    Format: "Figure X: caption text" (centred, 10pt)
    """
    caption_para = doc.add_paragraph()
    caption_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    # Sprachspezifisches Label
    figure_labels = _word("figure")
    label = _fixed_text(figure_labels, lang)
    
    run = caption_para.add_run(label)
    run.font.size = Pt(10)
    
    # SEQ field for automatic numbering
    _add_seq_field_standalone(run, "Abbildung")
    
    # Caption-Text
    caption_run = caption_para.add_run(f": {caption_text}")
    caption_run.font.size = Pt(10)
    
    caption_para.paragraph_format.space_after = Pt(12)


def _add_seq_field_standalone(run, seq_name: str = "Abbildung"):
    """
    Adds a SEQ field for automatic numbering to a run.
    Standalone variant for functions outside the exporter class.
    """
    fld_char_begin = OxmlElement('w:fldChar')
    fld_char_begin.set(qn('w:fldCharType'), 'begin')
    run._r.append(fld_char_begin)
    
    instr_text = OxmlElement('w:instrText')
    instr_text.set(qn('xml:space'), 'preserve')
    instr_text.text = f' SEQ {seq_name} \\* ARABIC '
    run._r.append(instr_text)
    
    fld_char_separate = OxmlElement('w:fldChar')
    fld_char_separate.set(qn('w:fldCharType'), 'separate')
    run._r.append(fld_char_separate)
    
    placeholder = OxmlElement('w:t')
    placeholder.text = '1'
    run._r.append(placeholder)
    
    fld_char_end = OxmlElement('w:fldChar')
    fld_char_end.set(qn('w:fldCharType'), 'end')
    run._r.append(fld_char_end)


def _get_png_dimensions(png_bytes: bytes) -> tuple:
    """
    Reads width and height from a PNG header (bytes 16-23).
    
    Returns:
        (width, height) in pixels, or (None, None) on errors
    """
    try:
        if len(png_bytes) >= 24 and png_bytes[:8] == b'\x89PNG\r\n\x1a\n':
            import struct
            width = struct.unpack('>I', png_bytes[16:20])[0]
            height = struct.unpack('>I', png_bytes[20:24])[0]
            return (width, height)
    except Exception:
        pass
    return (None, None)


def _add_table(doc: Document, rows: list):
    """Adds a Markdown table as a Word table."""
    if not rows:
        return
    
    # parse the rows
    parsed_rows = []
    for row in rows:
        cells = [c.strip() for c in row.strip('|').split('|')]
        parsed_rows.append(cells)
    
    if not parsed_rows:
        return
    
    n_cols = max(len(r) for r in parsed_rows)
    n_rows = len(parsed_rows)
    
    table = doc.add_table(rows=n_rows, cols=n_cols)
    table.style = 'Table Grid'
    
    for row_idx, row_cells in enumerate(parsed_rows):
        for col_idx, cell_text in enumerate(row_cells):
            if col_idx < n_cols:
                cell = table.cell(row_idx, col_idx)
                cell.text = ''
                para = cell.paragraphs[0]
                _add_formatted_runs(para, cell_text.strip())
                para.paragraph_format.space_before = Pt(2)
                para.paragraph_format.space_after = Pt(2)
                
                # set the font size of the runs to 9pt
                for run in para.runs:
                    run.font.size = Pt(9)
                
                # header row in bold
                if row_idx == 0:
                    for run in para.runs:
                        run.bold = True
    
    # spacing after the table
    last_para = doc.add_paragraph()
    last_para.paragraph_format.space_before = Pt(0)
    last_para.paragraph_format.space_after = Pt(0)
    # keep the paragraph minimal
    run = last_para.add_run()
    run.font.size = Pt(2)


def _add_horizontal_rule(doc: Document):
    """Adds a horizontal rule."""
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    
    # bottom border as a dividing line
    pPr = para._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'), 'single')
    bottom.set(qn('w:sz'), '6')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), 'CCCCCC')
    pBdr.append(bottom)
    pPr.append(pBdr)


def _flush_numbered_list(doc: Document, items: list):
    """Adds numbered list items."""
    for i, item in enumerate(items):
        para = doc.add_paragraph(style='List Number')
        _add_formatted_runs(para, item)


def _flush_list_items(doc: Document, items: list):
    """Adds the collected list items as a bullet list."""
    for item in items:
        para = doc.add_paragraph(style='List Bullet')
        _add_formatted_runs(para, item)


def _add_formatted_paragraph(doc: Document, text: str):
    """Adds a paragraph with inline formatting."""
    para = doc.add_paragraph()
    _add_formatted_runs(para, text)


def _add_formatted_runs(para, text: str):
    """
    Adds text with inline formatting (bold, italic, code, links) to a paragraph.
    
    Supports:
    - **bold** or __bold__
    - *italic* or _italic_
    - `inline code`
    - [text](URL) -> clickable hyperlink
    - combinations such as **_bold and italic_**
    """
    # first extract the links, then apply the formatting to the parts
    # link pattern: [text](url)
    link_pattern = r'\[([^\]]+)\]\((https?://[^)]+)\)'
    
    # split the text into parts with and without links
    parts = re.split(link_pattern, text)
    # parts = [before_link, link_text, link_url, after_link, ...]
    
    i = 0
    while i < len(parts):
        if i + 2 < len(parts) and parts[i+2].startswith('http'):
            # before the link: normal formatting
            if parts[i]:
                _add_inline_formatted_runs(para, parts[i])
            # the link itself
            link_text = parts[i+1]
            link_url = parts[i+2]
            _add_hyperlink(para, link_url, link_text)
            i += 3
        else:
            # no link: normal formatting
            if parts[i]:
                _add_inline_formatted_runs(para, parts[i])
            i += 1


def _add_hyperlink(para, url: str, text: str):
    """Inserts a clickable hyperlink into a paragraph."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    
    # create the relationship for the URL
    part = para.part
    r_id = part.relate_to(url, RT.HYPERLINK, is_external=True)
    
    # Hyperlink-Element
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    
    # run with the link text
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    
    # blue and underlined (standard link style)
    color = OxmlElement('w:color')
    color.set(qn('w:val'), '0563C1')
    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rPr.append(color)
    rPr.append(u)
    new_run.append(rPr)
    
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = text
    new_run.append(t)
    
    hyperlink.append(new_run)
    para._p.append(hyperlink)


def _add_inline_formatted_runs(para, text: str):
    """Adds text with inline formatting (bold, italic, code), without links."""
    # regex for the formattings (including inline code)
    pattern = r'(`[^`]+`|\*\*.*?\*\*|__.*?__|_.*?_|\*[^*]+\*)'
    
    parts = re.split(pattern, text)
    
    for part in parts:
        if not part:
            continue
        
        # Inline-Code: `text`
        if part.startswith('`') and part.endswith('`') and len(part) > 2:
            inner = part[1:-1]
            run = para.add_run(inner)
            run.font.name = 'Consolas'
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(51, 51, 51)
        
        # bold: **text** or __text__
        elif (part.startswith('**') and part.endswith('**')) or \
           (part.startswith('__') and part.endswith('__')):
            inner = part[2:-2]
            # check whether also italic (_text_ inside)
            if inner.startswith('_') and inner.endswith('_'):
                run = para.add_run(inner[1:-1])
                run.bold = True
                run.italic = True
            else:
                run = para.add_run(inner)
                run.bold = True
        
        # italic: _text_ or *text*
        elif (part.startswith('_') and part.endswith('_') and len(part) > 2) or \
             (part.startswith('*') and part.endswith('*') and not part.startswith('**')):
            inner = part[1:-1]
            run = para.add_run(inner)
            run.italic = True
        
        # normal text
        else:
            para.add_run(part)


def _cluster_heading_text(cluster_name: str, count: int, unique_respondents: int, lang: str) -> str:
    """Consistent heading text of a cluster.
    
    Item mode (count != unique_respondents):
        "Name (76 mentions by 57 persons)"
    Raw mode (count == unique_respondents or unique_respondents == 0):
        "Name (67 mentions)"
    """
    mentions = _fixed_text(_word("mentions"), lang)
    persons = _fixed_text(_word("persons"), lang)
    of_word = _fixed_text(_word("of"), lang)
    
    if count > 0 and unique_respondents > 0 and count != unique_respondents:
        return f"{cluster_name} ({count} {mentions} {of_word} {unique_respondents} {persons})"
    elif count > 0:
        return f"{cluster_name} ({count} {mentions})"
    elif unique_respondents > 0:
        return f"{cluster_name} ({unique_respondents} {mentions})"
    else:
        return cluster_name


class WordExporter:
    """Creates Word documents of survey results."""
    
    def __init__(self, output_dir: Optional[Path] = None, template_path: Optional[Path] = None):
        """
        Initialises the WordExporter.
        
        Args:
            output_dir: output directory of the documents
            template_path: optional path of a Word template
        """
        self.output_dir = Path(output_dir) if output_dir else Path("outputs")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.template_path = template_path
        self.tm = get_translation_manager()
    
    def _create_document(
        self, lang: str = "de", title: str = "", subtitle: str = ""
    ) -> Document:
        """Creates a new document (with or without a template)."""
        if self.template_path and self.template_path.exists():
            doc = Document(self.template_path)
        else:
            doc = Document()
            
            # adjust the default styles
            style = doc.styles['Normal']
            style.font.name = 'Calibri'
            style.font.size = Pt(11)
            
            # heading styles with a clear size hierarchy
            # H1: parts (Part I, Part II, ...), 20pt
            # H2: question groups / chapters, 16pt
            # H3: single questions, 14pt (must stand out from H4)
            # H4: subsections (clusters, segments), 12pt
            heading_sizes = {1: 18, 2: 16, 3: 14, 4: 12}
            for level, size in heading_sizes.items():
                heading_style = doc.styles[f'Heading {level}']
                heading_style.font.name = 'Calibri'
                heading_style.font.bold = True
                heading_style.font.size = Pt(size)
        
        # add a footer with page numbers (and optionally the title)
        self._add_page_footer(doc, lang, title=title, subtitle=subtitle)
        
        return doc
    
    def _add_page_footer(
        self, doc: Document, lang: str = "de",
        title: str = "", subtitle: str = ""
    ):
        """
        Adds a footer: title + subtitle on the left, page number on the right.
        
        Layout: "Title - subtitle                    Page X of Y"
        
        Uses the Word fields PAGE and NUMPAGES for dynamic page numbers.
        """
        # footer texts per language
        footer_labels = _word("footer")
        prefix, separator = _fixed_text(footer_labels, lang)
        
        section = doc.sections[0]
        footer = section.footer
        footer.is_linked_to_previous = False
        
        # empty the existing paragraphs
        if footer.paragraphs:
            para = footer.paragraphs[0]
            para.clear()
        else:
            para = footer.add_paragraph()
        
        # right-aligned tab stop at the right margin
        from docx.shared import Cm
        from docx.enum.text import WD_TAB_ALIGNMENT
        page_width = section.page_width
        left_margin = section.left_margin
        right_margin = section.right_margin
        text_width = page_width - left_margin - right_margin
        
        tab_stops = para.paragraph_format.tab_stops
        tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
        
        # === left: title - subtitle ===
        if title:
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
        run_prefix = para.add_run(prefix)
        run_prefix.font.size = Pt(9)
        run_prefix.font.color.rgb = RGBColor(128, 128, 128)
        
        # PAGE field (current page number); fldChar approach for reliable formatting
        _add_field_fldchar(para, ' PAGE ', '1')
        
        # " of "
        run_sep = para.add_run(separator)
        run_sep.font.size = Pt(9)
        run_sep.font.color.rgb = RGBColor(128, 128, 128)
        
        # NUMPAGES field (total number of pages)
        _add_field_fldchar(para, ' NUMPAGES ', '1')
    
    def _add_help_text(
        self,
        doc: Document,
        help_text: Optional[str],
        italic: bool = True
    ):
        """
        Adds an explanation (help text) after the question title.
        
        Args:
            doc: Word document
            help_text: the explanation (may be None)
            italic: whether the text is italic (default: True)
        """
        if not help_text:
            return
        
        para = doc.add_paragraph()
        run = para.add_run(help_text)
        run.italic = italic
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(100, 100, 100)  # grey
        para.space_after = Pt(12)
    
    def _add_figure(
        self,
        doc: Document,
        fig: plt.Figure,
        width: float = 6.0
    ):
        """
        Adds a matplotlib figure to the document.
        
        Args:
            doc: Word document
            fig: matplotlib figure
            width: width in inches
        """
        # save the figure in a BytesIO
        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight',
                   facecolor='white', edgecolor='none', dpi=300)
        buf.seek(0)
        
        # add it to the document
        doc.add_picture(buf, width=Inches(width))
        
        # centre
        last_paragraph = doc.paragraphs[-1]
        last_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        plt.close(fig)
    
    def _add_figure_with_caption(
        self,
        doc: Document,
        fig: plt.Figure,
        caption_text: str,
        n: int = None,
        width: float = 5.5,
        lang: str = "de"
    ):
        """
        Adds a matplotlib figure with a caption to the document.
        
        The caption carries automatic numbering (SEQ field), which Word
        updates and lists in the list of figures.
        
        Args:
            doc: Word document
            fig: matplotlib figure
            caption_text: caption text (the question text)
            n: sample size (optional)
            width: width in inches
            lang: language
        """
        # save the figure in a BytesIO
        buf = BytesIO()
        fig.savefig(buf, format='png', bbox_inches='tight',
                   facecolor='white', edgecolor='none', dpi=300)
        buf.seek(0)
        
        # add the image to the document
        doc.add_picture(buf, width=Inches(width))
        
        # centre the image + keep_with_next: prevents a page break between the
        # image and its caption
        pic_paragraph = doc.paragraphs[-1]
        pic_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _set_keep_with_next(pic_paragraph)
        
        plt.close(fig)
        
        # add the caption
        caption_para = doc.add_paragraph()
        caption_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # "Figure " text
        figure_labels = _word("figure")
        figure_label = _fixed_text(figure_labels, lang)
        
        run = caption_para.add_run(figure_label)
        run.font.size = Pt(10)
        
        # insert the SEQ field for automatic numbering
        self._figure_no = getattr(self, "_figure_no", 0) + 1
        self._add_seq_field(run, "Abbildung", placeholder=str(self._figure_no))
        
        # separator and caption text
        full_caption = f": {caption_text}"
        if n is not None:
            full_caption += f" (n={n})"
        
        caption_run = caption_para.add_run(full_caption)
        caption_run.font.size = Pt(10)
        
        # spacing after the caption
        caption_para.space_after = Pt(12)
    
    def _add_seq_field(self, run, seq_name: str = "Abbildung", placeholder: str = "1"):
        """
        Adds a SEQ field for automatic numbering to a run.
        
        Args:
            run: the run that gets the field
            seq_name: name of the sequence (e.g. "Abbildung", "Table")
        """
        # field begin
        fld_char_begin = OxmlElement('w:fldChar')
        fld_char_begin.set(qn('w:fldCharType'), 'begin')
        run._r.append(fld_char_begin)
        
        # field instruction
        instr_text = OxmlElement('w:instrText')
        instr_text.set(qn('xml:space'), 'preserve')
        instr_text.text = f' SEQ {seq_name} \\* ARABIC '
        run._r.append(instr_text)
        
        # field separator
        fld_char_separate = OxmlElement('w:fldChar')
        fld_char_separate.set(qn('w:fldCharType'), 'separate')
        run._r.append(fld_char_separate)
        
        # placeholder text (updated by Word)
        placeholder_el = OxmlElement('w:t')
        placeholder_el.text = placeholder
        run._r.append(placeholder_el)
        
        # field end
        fld_char_end = OxmlElement('w:fldChar')
        fld_char_end.set(qn('w:fldCharType'), 'end')
        run._r.append(fld_char_end)
    
    def _add_table_of_figures(self, doc: Document, lang: str = "de"):
        """
        Adds a list of figures to the document.
        
        Args:
            doc: Word document
            lang: language
        """
        # heading
        tof_titles = _word("list_of_figures")
        doc_para = doc.add_paragraph(_fixed_text(tof_titles, lang))
        doc_para.style = doc.styles['TOC Heading']
        
        # TOC field for the list of figures
        para = doc.add_paragraph()
        
        run = para.add_run()
        
        # field begin
        fld_char_begin = OxmlElement('w:fldChar')
        fld_char_begin.set(qn('w:fldCharType'), 'begin')
        run._r.append(fld_char_begin)
        
        # field instruction for the list of figures
        instr_text = OxmlElement('w:instrText')
        instr_text.set(qn('xml:space'), 'preserve')
        instr_text.text = ' TOC \\c "Abbildung" '
        run._r.append(instr_text)
        
        # field separator
        fld_char_separate = OxmlElement('w:fldChar')
        fld_char_separate.set(qn('w:fldCharType'), 'separate')
        run._r.append(fld_char_separate)
        
        # placeholder
        placeholder_run = para.add_run()
        placeholder_labels = _word("list_of_figures_hint")
        placeholder_run.text = _fixed_text(placeholder_labels, lang)
        placeholder_run.font.italic = True
        placeholder_run.font.color.rgb = None  # grey
        
        # field end
        end_run = para.add_run()
        fld_char_end = OxmlElement('w:fldChar')
        fld_char_end.set(qn('w:fldCharType'), 'end')
        end_run._r.append(fld_char_end)
        
        doc.add_page_break()
    
    def create_question_document(
        self,
        question_id: str,
        question_text: str,
        fig: plt.Figure,
        n_info: str,
        lang: str = "de",
        additional_content: Optional[dict] = None,
        n: int = None,
        help_text: Optional[str] = None
    ) -> Document:
        """
        Creates a Word document for a question group.
        
        Args:
            question_id: id of the question
            question_text: question text
            fig: chart
            n_info: n information (e.g. "n=50" or "120 mentions by 50 persons in total")
            lang: language
            additional_content: additional content (e.g. a list of other answers)
            n: sample size as a number (for the caption)
            help_text: explanation of the question (optional)
        
        Returns:
            Word document
        """
        doc = self._create_document(lang)
        
        # title
        doc.add_heading(question_text, level=1)
        
        # explanation (help text) after the title
        self._add_help_text(doc, help_text)
        
        # take n from n_info if not given explicitly
        if n is None and n_info:
            import re
            match = re.search(r'n\s*=\s*(\d+)', n_info)
            if match:
                n = int(match.group(1))
        
        # chart with caption
        self._add_figure_with_caption(
            doc=doc,
            fig=fig,
            caption_text=question_text,
            n=n,
            width=6.0,
            lang=lang
        )
        
        # additional content (e.g. "Other" mentions)
        if additional_content:
            if "sonstiges" in additional_content:
                further_text = _text("clustering.other_mentions", lang)
                doc.add_heading(further_text, level=2)
                
                for item in additional_content["sonstiges"]:
                    para = doc.add_paragraph(style='List Bullet')
                    para.add_run(item)
        
        return doc
    
    def create_cluster_document(
        self,
        question_id: str,
        question_text: str,
        clusters: list[dict],
        fig: plt.Figure,
        n: int,
        n_info: str = None,
        lang: str = "de",
        help_text: Optional[str] = None
    ) -> Document:
        """
        Creates a Word document for free-text clustering.
        
        Args:
            question_id: id of the question
            question_text: question text
            clusters: list of the clusters with names, descriptions, examples
            fig: chart
            n: sample size
            n_info: n information (e.g. "120 mentions by 50 persons in total")
            lang: language
            help_text: explanation of the question (optional)
        
        Returns:
            Word document
        """
        doc = self._create_document(lang)
        
        # title
        doc.add_heading(question_text, level=1)
        
        # explanation (help text) after the title
        self._add_help_text(doc, help_text)
        
        # chart with caption
        self._add_figure_with_caption(
            doc=doc,
            fig=fig,
            caption_text=question_text,
            n=n,
            width=5.5,
            lang=lang
        )
        
        # heading of the cluster details
        cluster_heading = _fixed_text(_word("cluster_details"), lang)
        doc.add_heading(cluster_heading, level=2)
        
        # Cluster-Details
        for cluster in clusters:
            # cluster name: string or dict (multilingual)
            cluster_name = cluster.get('name', '')
            if isinstance(cluster_name, dict):
                cluster_name = _config_text(cluster_name, lang, str(cluster_name))
            
            # cluster name with the count as a subheading
            unique_respondents = cluster.get('unique_respondents', 0)
            count = cluster.get('count', 0)
            heading_text = _cluster_heading_text(cluster_name, count, unique_respondents, lang)
            doc.add_heading(heading_text, level=3)
            
            # description: string or dict
            description = cluster.get('description', '')
            if isinstance(description, dict):
                description = _config_text(description, lang, '')
            if description:
                para = doc.add_paragraph()
                run = para.add_run(description)
                run.italic = True
            
            # example answers, translated if available
            examples_translated = cluster.get('examples_translated', {})
            if examples_translated and lang in examples_translated and examples_translated[lang]:
                examples = examples_translated[lang]
            else:
                examples = cluster.get('examples', [])
            
            if examples:
                examples_text = _fixed_text(_word("examples"), lang)
                doc.add_paragraph(examples_text)
                for example in examples:
                    if example:  # only non-empty examples
                        para = doc.add_paragraph(style='List Bullet')
                        run = para.add_run(f'"{example}"')
                        run.italic = True
            
            # spacing after every cluster
            doc.add_paragraph()
        
        return doc
    
    def add_cluster_details_section(
        self,
        doc: Document,
        clusters: list[dict],
        lang: str = "de"
    ):
        """
        Adds a section with cluster details to an existing document.
        
        Args:
            doc: existing Word document
            clusters: list of the clusters with names, descriptions, examples
            lang: language
        """
        if not clusters:
            return
        
        # heading
        cluster_heading = _fixed_text(_word("cluster_details"), lang)
        doc.add_heading(cluster_heading, level=2)
        
        for cluster in clusters:
            # cluster name (language-specific if present)
            cluster_name = cluster.get('name', '')
            if isinstance(cluster_name, dict):
                cluster_name = _config_text(cluster_name, lang, str(cluster_name))
            
            # cluster name with the count
            unique_respondents = cluster.get('unique_respondents', 0)
            count = cluster.get('count', 0)
            heading_text = _cluster_heading_text(cluster_name, count, unique_respondents, lang)
            doc.add_heading(heading_text, level=3)
            
            # description (language-specific if present)
            description = cluster.get('description', '')
            if isinstance(description, dict):
                description = _config_text(description, lang, '')
            
            if description:
                para = doc.add_paragraph()
                run = para.add_run(description)
                run.italic = True
            
            # example answers (language-specific if present)
            examples_translated = cluster.get('examples_translated', {})
            if examples_translated and lang in examples_translated and examples_translated[lang]:
                examples_to_show = examples_translated[lang]
            else:
                examples_to_show = cluster.get('examples', [])
            
            if examples_to_show:
                examples_text = _fixed_text(_word("examples"), lang)
                doc.add_paragraph(examples_text)
                for example in examples_to_show[:3]:
                    if example:  # only non-empty examples
                        para = doc.add_paragraph(style='List Bullet')
                        run = para.add_run(f'"{example}"')
                        run.italic = True
            
            doc.add_paragraph()  # spacing
        
        return doc
    
    def export_question(
        self,
        question_id: str,
        result,  # AnalysisResult or dict
        config: dict,
        lang: str = "de"
    ) -> Path:
        """
        Exports a question as a Word document.
        
        Args:
            question_id: id of the question
            result: analysis result (AnalysisResult or dict)
            config: configuration of the question group
            lang: language
        
        Returns:
            path of the saved file
        """
        from handlers import get_handler
        
        # question text from the configuration
        question_text = config.get("label", {}).get(lang, question_id)
        
        # extract the AnalysisResult attributes
        if hasattr(result, 'n'):
            n = result.n
            additional_content = result.additional_content
            metadata = result.metadata if hasattr(result, 'metadata') else {}
        else:
            n = result.get("n", 0)
            additional_content = result.get("additional_content")
            metadata = result.get("metadata", {})
        
        # cluster details from the metadata (already a list of dicts)
        cluster_details = metadata.get('cluster_details', [])
        
        # build the n information (special case: free text with items)
        handler_type = config.get('handler', 'single_choice')
        if handler_type == 'freetext' and metadata.get('analysis_mode') in ('items', 'fields'):
            # n = unique respondents
            # total_items = number of extracted items
            n_responses = n  # n is unique_responses already
            n_items = metadata.get('total_items', n)  # fall back to n if missing
            
            if n_items != n_responses:
                n_info = text("handler.freetext.chart_items", lang, n_items=n_items, n_responses=n_responses)
            else:
                n_info = f"n={n_responses}"
        else:
            n_info = f"n={n}"
        
        # get the handler for the chart
        handler = get_handler(
            handler_type,
            question_id,
            config
        )
        
        # create the chart
        default_chart = config.get('default_chart', 'bar_horizontal')
        fig = handler.create_chart(result, default_chart, lang=lang)
        
        # create the document
        if cluster_details:
            doc = self.create_cluster_document(
                question_id=question_id,
                question_text=question_text,
                clusters=cluster_details,
                fig=fig,
                n=n,
                n_info=n_info,
                lang=lang
            )
        else:
            doc = self.create_question_document(
                question_id=question_id,
                question_text=question_text,
                fig=fig,
                n_info=n_info,
                lang=lang,
                additional_content=additional_content
            )
        
        # save
        filepath = self.output_dir / f"{question_id}_{lang}.docx"
        return self.save_document(doc, filepath)
    
    def save_document(
        self,
        doc: Document,
        filepath: Path
    ) -> Path:
        """
        Saves a Word document.
        
        Args:
            doc: Word document
            filepath: output path
        
        Returns:
            path of the saved file
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        doc.save(filepath)
        logger.info(f"Word document saved: {filepath}")
        return filepath
    
    def add_comparison_table(
        self,
        doc: Document,
        comparison_data: dict,
        title: str = "",
        show_counts: bool = False,
        lang: str = "de"
    ):
        """
        Adds a comparison table to the document.
        
        Args:
            doc: Word document
            comparison_data: dict with:
                - columns: [{'name': str, 'n': int, 'warning': str|None}, ...]
                - rows: [{'label': str, 'values': [{'count': int, 'percent': float}, ...]}, ...]
            title: optional heading of the table
            show_counts: show absolute numbers too (not only percentages)
            lang: language
        """
        from docx.shared import RGBColor
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import nsdecls
        from docx.oxml import parse_xml
        
        columns = comparison_data.get('columns', [])
        rows = comparison_data.get('rows', [])
        
        if not columns or not rows:
            return
        
        # add the title
        if title:
            doc.add_heading(title, level=2)
        
        # create the table: 1 label column + n segment columns
        n_cols = 1 + len(columns)
        n_rows = 1 + len(rows)  # +1 for the header
        
        table = doc.add_table(rows=n_rows, cols=n_cols)
        table.style = 'Table Grid'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # column widths:
        # label column wide (6 cm), segment columns evenly distributed,
        # total width about 15 cm
        total_width_cm = 15
        label_width_cm = 6
        remaining_width = total_width_cm - label_width_cm
        segment_width_cm = remaining_width / len(columns) if columns else 2
        
        widths = [Cm(label_width_cm)] + [Cm(segment_width_cm)] * len(columns)
        self._set_column_widths(table, widths)
        
        # header row
        header_row = table.rows[0]
        header_row.cells[0].text = ""  # empty corner
        
        for i, col in enumerate(columns):
            cell = header_row.cells[i + 1]
            # header with n and a warning if any
            header_text = col['name']
            if col.get('warning'):
                header_text += f"\n(n={col['n']}, {col['warning']})"
            else:
                header_text += f"\n(n={col['n']})"
            
            cell.text = header_text
            
            # format the header
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(9)
            
            # background colour of the header
            shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="E7E6E6"/>')
            cell._tc.get_or_add_tcPr().append(shading)
        
        # data rows
        for row_idx, row_data in enumerate(rows):
            table_row = table.rows[row_idx + 1]
            
            # Label
            label_cell = table_row.cells[0]
            label_cell.text = str(row_data['label'])
            for paragraph in label_cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)
            
            # values
            for col_idx, value in enumerate(row_data['values']):
                cell = table_row.cells[col_idx + 1]
                
                if show_counts:
                    cell_text = f"{value['percent']}%\n({value['count']})"
                else:
                    cell_text = f"{value['percent']}%"
                
                cell.text = cell_text
                
                for paragraph in cell.paragraphs:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.font.size = Pt(9)
        
        # some spacing after the table
        doc.add_paragraph()
    
    def add_simple_table(
        self,
        doc: Document,
        data: dict,
        percentages: dict = None,
        n: int = 0,
        warning: str = None,
        title: str = "",
        lang: str = "de"
    ):
        """
        Adds a simple result table (for the overall results).
        
        Args:
            doc: Word document
            data: dict {label: count}
            percentages: dict {label: percent}
            n: sample size
            warning: optional warning
            title: optional heading
            lang: language
        """
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import nsdecls
        from docx.oxml import parse_xml
        
        if not data:
            return
        
        # title
        if title:
            heading = doc.add_heading(title, level=2)
        
        # n information with a warning
        n_text = f"n = {n}"
        if warning:
            n_text += f" ({warning})"
        n_para = doc.add_paragraph(n_text)
        n_para.runs[0].font.italic = True
        n_para.runs[0].font.size = Pt(9)
        
        # table
        n_rows = len(data) + 1  # +1 for the header
        table = doc.add_table(rows=n_rows, cols=3)
        table.style = 'Table Grid'
        
        # column widths: answer wide, count/percent narrow
        # total width about 15 cm: answer 11 cm, count 2 cm, percent 2 cm
        self._set_column_widths(table, [Cm(11), Cm(2), Cm(2)])
        
        # Header
        headers = _word("answer_headers")
        header_texts = _fixed_text(headers, lang)
        
        header_row = table.rows[0]
        for i, text in enumerate(header_texts):
            cell = header_row.cells[i]
            cell.text = text
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                    run.font.size = Pt(9)
            shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="E7E6E6"/>')
            cell._tc.get_or_add_tcPr().append(shading)
        
        # data (sorted by count, descending)
        sorted_data = sorted(data.items(), key=lambda x: x[1] if isinstance(x[1], (int, float)) else 0, reverse=True)
        
        for row_idx, (label, count) in enumerate(sorted_data):
            row = table.rows[row_idx + 1]
            row.cells[0].text = str(label)
            row.cells[1].text = str(count)
            
            pct = percentages.get(label, 0) if percentages else 0
            row.cells[2].text = f"{pct}%"
            
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.size = Pt(9)
        
        doc.add_paragraph()
    
    def _set_column_widths(self, table, widths: list):
        """
        Sets the column widths of a table.
        
        Args:
            table: Word table
            widths: list of widths (docx.shared.Cm or docx.shared.Inches)
        """
        for i, width in enumerate(widths):
            if i < len(table.columns):
                for cell in table.columns[i].cells:
                    cell.width = width
    
    def create_full_report(
        self,
        results: list[dict],
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de",
        include_toc: bool = True,
        report_groups: dict = None,
        config: dict = None
    ) -> Document:
        """
        Creates a complete report with all analyses.
        
        Args:
            results: list of dictionaries with:
                - question_id: id of the question
                - question_text: question text
                - fig: matplotlib figure
                - n_info: n information
                - cluster_details: optional, list of cluster dicts
                - unique_respondents: optional, number of distinct persons
            title: report title
            subtitle: subtitle (e.g. the date)
            lang: language
            include_toc: insert a table of contents
            report_groups: dictionary with the question groups of the configuration
            config: complete configuration for additional information
        
        Returns:
            Word document
        """
        self._figure_no = 0   # figure numbers start at 1 in every report
        from datetime import datetime
        from docx.enum.section import WD_ORIENT
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement
        
        doc = self._create_document(lang, title=title, subtitle=subtitle)
        
        # === TITLE PAGE ===
        title_para = doc.add_paragraph()
        title_run = title_para.add_run(title)
        title_run.bold = True
        title_run.font.size = Pt(28)
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_para.space_after = Pt(12)
        
        if subtitle:
            subtitle_para = doc.add_paragraph()
            subtitle_run = subtitle_para.add_run(subtitle)
            subtitle_run.font.size = Pt(16)
            subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        date_para = doc.add_paragraph()
        date_str = datetime.now().strftime("%d.%m.%Y")
        date_labels = _word("generated_on", date=date_str)
        date_run = date_para.add_run(_fixed_text(date_labels, lang))
        date_run.font.size = Pt(11)
        date_run.italic = True
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        count_para = doc.add_paragraph()
        count_labels = _word("analyses_count", count=len(results))
        count_run = count_para.add_run(_fixed_text(count_labels, lang))
        count_run.font.size = Pt(11)
        count_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_page_break()
        
        # === TABLE OF CONTENTS ===
        if include_toc:
            toc_titles = _word("toc")
            # TOC heading: outlineLevel=9 -> does NOT appear in the TOC
            toc_para = doc.add_paragraph(_fixed_text(toc_titles, lang))
            toc_para.style = doc.styles['TOC Heading']
            
            para = doc.add_paragraph()
            fld_simple = OxmlElement('w:fldSimple')
            fld_simple.set(qn('w:instr'), 'TOC \\o "1-3" \\h \\z \\u')
            run_element = OxmlElement('w:r')
            text_element = OxmlElement('w:t')
            placeholder_labels = _word("toc_hint")
            text_element.text = _fixed_text(placeholder_labels, lang)
            run_element.append(text_element)
            fld_simple.append(run_element)
            para._p.append(fld_simple)
            
            doc.add_page_break()
        
        # === PART I: QUESTION RESULTS ===
        part1_titles = _word("part1_questions")
        doc.add_heading(_fixed_text(part1_titles, lang), level=1)
        
        # build an index for quick access to the results
        results_by_id = {r.get('question_id'): r for r in results}
        
        # organise the questions by group
        if report_groups:
            # sort the groups by order, "general" last
            sorted_groups = sorted(
                report_groups.items(),
                key=lambda x: (
                    100 if x[0] == 'general' else x[1].get('order', 99),
                    x[0]
                )
            )
            
            for group_id, group_data in sorted_groups:
                group_label = group_data.get('label', {})
                group_title = _config_text(group_label, lang, group_id)
                group_questions = group_data.get('questions', [])
                
                # does at least one question have results?
                group_results = [results_by_id.get(qid) for qid in group_questions if qid in results_by_id]
                if not group_results:
                    continue
                
                # group heading (level 2)
                doc.add_heading(group_title, level=2)
                
                # question headings: H3 (in the TOC) unless toc_questions is set to
                # false explicitly, otherwise H4 (not visible in the TOC)
                show_in_toc = group_data.get('toc_questions', True)
                q_heading_level = 3 if show_in_toc else 4
                
                # questions of the group
                for qid in group_questions:
                    result = results_by_id.get(qid)
                    if not result:
                        continue
                    
                    self._add_question_to_report(doc, result, lang, heading_level=q_heading_level)
        else:
            # fallback: all questions without grouping
            for i, result in enumerate(results):
                self._add_question_to_report(doc, result, lang, heading_level=2)
                if i < len(results) - 1:
                    doc.add_page_break()
        
        return doc
    
    def _add_question_to_report(
        self, 
        doc: Document, 
        result: dict, 
        lang: str,
        heading_level: int = 2
    ):
        """
        Adds one question to the report.
        
        Args:
            doc: Word document
            result: dictionary with the question data (including an optional 'summary')
            lang: language
            heading_level: heading level (2 or 3)
        """
        question_id = result.get('question_id', 'unknown')
        question_text = result.get('question_text', question_id)
        n = result.get('n')
        unique_respondents = result.get('unique_respondents', n)
        summary = result.get('summary')  # QuestionSummary or dict
        
        # clean the heading (without filling instructions)
        clean_title = clean_question_title(question_text)
        
        # Debug-Logging
        logger.debug(f"Word report question: qid={question_id}, clean_title='{clean_title[:60]}...'")
        
        # heading
        doc.add_heading(clean_title, level=heading_level)
        
        # === INTRODUCTION: n information ===
        self._add_n_info_sentence(doc, n, unique_respondents, result.get('cluster_details'), lang)
        
        # === LLM SUMMARY (BEFORE charts and tables) ===
        if summary:
            self._add_question_summary(doc, summary, lang, heading_level)
        elif result.get('description_text'):
            # without an LLM summary: the description of the handler in the report language
            doc.add_paragraph(result['description_text'])
        
        # === NUMERIC: statistics table (before the chart, for numeric questions) ===
        # the result may be a dict (pipeline format) or an AnalysisResult object;
        # numeric_stats come from the metadata field, if present
        numeric_stats = None
        if isinstance(result, dict):
            numeric_stats = result.get('numeric_stats')
            if numeric_stats is None:
                meta = result.get('metadata') or {}
                numeric_stats = meta.get('numeric_stats')
        elif hasattr(result, 'metadata'):
            meta = getattr(result, 'metadata', {}) or {}
            numeric_stats = meta.get('numeric_stats')
        
        if numeric_stats is not None:
            try:
                self._add_numeric_stats_table(doc, numeric_stats, lang)
            except Exception as e:
                logger.warning(f"Numeric statistics table for {question_id} failed: {e}")
        
        # === NPS ===
        nps = result.get('nps') if isinstance(result, dict) else None
        if nps:
            para = doc.add_paragraph()
            para.add_run(_fixed_text(_word("nps", **{k: (f"{v:+.0f}" if k == "nps" else f"{v:.0f}") if isinstance(v, float)
                                                     else v for k, v in nps.items()}), lang)).bold = True
        
        # === SCALE METRICS: single choice with an ordinal scale, matrix rows ===
        scale_metrics = result.get('scale_metrics') if isinstance(result, dict) else None
        if scale_metrics:
            try:
                self._add_scale_metrics_table(doc, scale_metrics, lang)
            except Exception as e:
                logger.warning(f"Scale metrics for {question_id} failed: {e}")
        
        # === NUMERIC_MATRIX: one row per field (handlers/numeric_matrix.py) ===
        meta = (result.get('metadata') or {}) if isinstance(result, dict) else (getattr(result, 'metadata', {}) or {})
        if meta.get('item_stats'):
            try:
                self._add_numeric_matrix_table(doc, meta['item_stats'], meta.get('constant_sum'), lang)
            except Exception as e:
                logger.warning(f"Table of the number fields for {question_id} failed: {e}")
        
        # chart with caption
        # free text with only 1 cluster: no chart (a single bar at 100% says nothing)
        cluster_details = result.get('cluster_details', [])
        skip_chart = (len(cluster_details) == 1 and result.get('fig'))
        
        if result.get('fig') and not skip_chart:
            self._add_figure_with_caption(
                doc=doc,
                fig=result['fig'],
                caption_text=clean_title,
                n=n,
                width=6.0,
                lang=lang
            )
        elif skip_chart:
            # close the figure, as it is not inserted
            import matplotlib.pyplot as plt
            plt.close(result['fig'])
        
        # table for a segment question
        if result.get('segment_table_data'):
            self._add_segment_table(doc, result['segment_table_data'], n, lang,
                                    result.get('segment_table_dimension'))
        
        # score explanation (for ranking questions)
        if result.get('score_explanation'):
            score_para = doc.add_paragraph()
            score_run = score_para.add_run(result['score_explanation'])
            score_run.italic = True
            score_run.font.size = Pt(9)
            score_para.space_after = Pt(12)
        
        # cluster details (for free text)
        if result.get('cluster_details'):
            cluster_level = heading_level + 1  # one level deeper
            for cluster in result['cluster_details']:
                cluster_name = cluster.get('name', '')
                if isinstance(cluster_name, dict):
                    cluster_name = _config_text(cluster_name, lang, str(cluster_name))
                
                # prefer unique_respondents (persons) if present, otherwise count (items)
                unique_respondents = cluster.get('unique_respondents', 0)
                count = cluster.get('count', 0)
                heading_text = _cluster_heading_text(cluster_name, count, unique_respondents, lang)
                doc.add_heading(heading_text, level=min(cluster_level, 4))  # Max Level 4
                
                description = cluster.get('description', '')
                if isinstance(description, dict):
                    description = _config_text(description, lang, '')
                if description:
                    doc.add_paragraph(description)
                
                examples_translated = cluster.get('examples_translated', {})
                examples_to_show = examples_translated.get(lang, []) if examples_translated else []
                if not examples_to_show:
                    examples_to_show = cluster.get('examples', [])
                
                if examples_to_show:
                    examples_labels = _word("examples")
                    doc.add_paragraph(examples_labels.get(lang, "Examples:"))
                    for example in examples_to_show[:3]:
                        if example:
                            para = doc.add_paragraph(style='List Bullet')
                            run = para.add_run(f'"{example}"')
                            run.italic = True
        
        # page break after every question
        doc.add_page_break()
    
    def _add_n_info_sentence(
        self, 
        doc: Document, 
        n: int, 
        unique_respondents: int,
        has_clusters: bool,
        lang: str
    ):
        """
        Adds an explanatory sentence on the number of answers.
        
        Args:
            doc: Word document
            n: number of answers/mentions
            unique_respondents: number of distinct persons
            has_clusters: whether there are cluster details (free-text question)
            lang: language
        """
        if not n:
            return
        
        # different wording depending on the question type
        if has_clusters and unique_respondents and unique_respondents != n:
            # free-text question with several mentions per person
            sentences = _word("mentions_from_persons", n=n, unique=unique_respondents)
        else:
            # standard question
            sentences = _word("answered_by", n=n)
        
        para = doc.add_paragraph()
        run = para.add_run(_fixed_text(sentences, lang))
        run.italic = True
        run.font.size = Pt(10)
        para.space_after = Pt(12)
    
    def _add_question_summary(
        self,
        doc: Document,
        summary,
        lang: str,
        heading_level: int = 3
    ):
        """
        Adds the summary of a question written by the LLM.
        
        Structure:
        1. "Description of the results" (heading)
        2. "Interpretation" (heading)
        3. "Segment differences" (heading), only with content
        
        Args:
            doc: Word document
            summary: QuestionSummary or dict with description, interpretation, segments_text
            lang: language
            heading_level: base heading level
        """
        # the sections dict of the summary (three sources):
        # 1. summary.sections (dict)
        # 2. the properties of the default schema: summary.description/interpretation/segments_text
        # 3. a plain dict: summary.get(...)
        sections_dict = self._extract_sections(summary)
        if not sections_dict:
            return
        
        # at least one section must be filled (skip if all are empty)
        if not any(v.strip() for v in sections_dict.values() if isinstance(v, str)):
            return
        
        # order and labels of the sections:
        # - default sections (description/interpretation/segments_text):
        #   fixed order + multilingual headings (as in the previous reports)
        # - custom sections: order as in the dict, heading = section id
        ordered_sections = self._order_sections_for_render(sections_dict, summary, lang)
        
        summary_level = min(heading_level + 1, 4)
        
        for section_id, text, label_for_lang in ordered_sections:
            if not (text and text.strip()):
                continue
            doc.add_heading(label_for_lang, level=summary_level)
            para = doc.add_paragraph(text)
            para.space_after = Pt(8)
        
        # spacing after the summary block (before the chart)
        doc.add_paragraph()
    
    @staticmethod
    def _extract_sections(summary) -> dict:
        """{section_id: text} from a QuestionSummary, a dict or an object with the default properties."""
        # summary.sections is a dict
        if hasattr(summary, 'sections') and isinstance(getattr(summary, 'sections'), dict):
            return dict(summary.sections)
        # object with the three properties of the default schema
        if hasattr(summary, 'description'):
            return {
                'description':    getattr(summary, 'description', '') or '',
                'interpretation': getattr(summary, 'interpretation', '') or '',
                'segments_text':  getattr(summary, 'segments_text', '') or '',
            }
        # Plain dict
        if isinstance(summary, dict):
            # use 'sections' if it exists, otherwise the keys as sections
            if 'sections' in summary and isinstance(summary['sections'], dict):
                return dict(summary['sections'])
            return {
                'description':    summary.get('description', ''),
                'interpretation': summary.get('interpretation', ''),
                'segments_text':  summary.get('segments_text', ''),
            }
        return {}
    
    # multilingual headings for the section ids of the default schema
    # (identical texts as in the previous reports)
    _DEFAULT_HEADINGS = {
        'description': _word("section_description"),
        'interpretation': _word("section_interpretation"),
        'segments_text': _word("section_segments"),
    }
    _DEFAULT_ORDER = ['description', 'interpretation', 'segments_text']
    
    def _order_sections_for_render(self, sections_dict: dict, summary, lang: str = 'de'):
        """
        Order and labels for rendering.
        
        Returns:
            list of tuples (section_id, text, heading_label_in_lang).
        
        Logic:
        - if all section keys belong to the three section ids of the default
          schema, the fixed order with their headings is used; for the default
          schema (the most frequent case) the report has the default headings in the default order.
        - otherwise: section order as in the dict, heading = section id
          (a caller with a better source can use self.render_summary_with_schema).
        """
        keys = set(sections_dict.keys())
        
        # all keys are section ids of the default schema
        if keys.issubset(set(self._DEFAULT_ORDER)):
            ordered = []
            for sid in self._DEFAULT_ORDER:
                if sid in sections_dict:
                    label = _fixed_text(self._DEFAULT_HEADINGS[sid], lang)
                    ordered.append((sid, sections_dict[sid], label))
            return ordered
        
        # custom sections: the section ids are used as heading texts
        # (capitalised, underscores as spaces: a stopgap for when no schema resolver
        # is available. The clean way, passing the schema with its labels, is
        # self._render_summary_with_schema.)
        ordered = []
        for sid, text in sections_dict.items():
            # a section id of the default schema still gets the multilingual heading
            if sid in self._DEFAULT_HEADINGS:
                label = _fixed_text(self._DEFAULT_HEADINGS[sid], lang)
            else:
                label = sid.replace('_', ' ').capitalize()
            ordered.append((sid, text, label))
        return ordered
    
    def render_summary_with_schema(
        self,
        doc,
        summary,
        schema,
        lang: str,
        heading_level: int = 3,
    ):
        """
        Schema-aware variant of the summary renderer.
        
        Iterates over the sections defined in the schema and uses their
        multilingual label dicts for the headings. Called by the pipeline as
        soon as the resolved schema is available.
        
        Args:
            doc: Word document
            summary: QuestionSummary with a sections dict
            schema: SummarySchema with the list of sections
            lang: language
            heading_level: base heading level
        """
        sections_dict = self._extract_sections(summary)
        if not sections_dict:
            return
        if not any(v.strip() for v in sections_dict.values() if isinstance(v, str)):
            return
        
        summary_level = min(heading_level + 1, 4)
        
        for section in schema.sections:
            text = sections_dict.get(section.id, '')
            if not (text and text.strip()):
                continue
            label = section.get_label(lang) if hasattr(section, 'get_label') \
                    else _config_text(section.label, lang, section.id)
            doc.add_heading(label, level=summary_level)
            para = doc.add_paragraph(text)
            para.space_after = Pt(8)
        
        doc.add_paragraph()
    
    def _add_scale_metrics_table(self, doc, metrics_by_label: dict, lang: str = 'de'):
        """Mean, median, top/bottom box and n per row (one row without label for a single choice)."""
        rows = [(label, m) for label, m in metrics_by_label.items() if m]
        if not rows:
            return
        with_label = len(rows) > 1 or rows[0][0]
        box = rows[0][1].get('box', 2)
        headers = ([_fixed_text(_word("field"), lang)] if with_label else []) + [
            _fixed_text(_word("scale_mean"), lang), _fixed_text(_word("scale_median"), lang),
            _fixed_text(_word("scale_top"), lang).replace("{k}", str(box)),
            _fixed_text(_word("scale_bottom"), lang).replace("{k}", str(box)), "n"]
        table = doc.add_table(rows=1 + len(rows), cols=len(headers))
        try:
            table.style = 'Light Grid Accent 1'
        except Exception:
            pass
        for i, h in enumerate(headers):
            table.rows[0].cells[i].text = h
        for r, (label, m) in enumerate(rows, start=1):
            n_text = f"{m['n']}" + (f" (+{m['n_excluded']})" if m.get('n_excluded') else "")
            values = ([str(label)] if with_label else []) + [f"{m['mean']:.2f}", f"{m['median']:g}",
                                                             f"{m['top_box']:.1f} %", f"{m['bottom_box']:.1f} %", n_text]
            for c, v in enumerate(values):
                table.rows[r].cells[c].text = v
        note = doc.add_paragraph()
        m0 = rows[0][1]
        run = note.add_run(_fixed_text(_word("scale_note"), lang).replace("{min}", f"{m0['min_value']:g}")
                           .replace("{max}", f"{m0['max_value']:g}") + " " +
                           _fixed_text(_word("scale_boxes"), lang).replace("{top}", ", ".join(m0.get('top_labels', [])))
                           .replace("{bottom}", ", ".join(m0.get('bottom_labels', []))))
        run.italic = True
        run.font.size = Pt(9)
        doc.add_paragraph()

    def _add_numeric_matrix_table(self, doc, item_stats: dict, constant_sum: dict = None, lang: str = 'de'):
        """
        Table of a numeric_matrix question: one row per field with n, mean,
        median, SD, min, max (and the mean share of the total for a constant
        sum); below it the share of respondents who reach the total.
        """
        if not item_stats:
            return
        headers = [_fixed_text(_word("field"), lang)] + list(_fixed_text(_word("numeric_headers"), lang))
        shares = (constant_sum or {}).get('mean_share_of_total') or {}
        if constant_sum:
            headers.append(_fixed_text(_word("share_of_total"), lang))
        table = doc.add_table(rows=1 + len(item_stats), cols=len(headers))
        try:
            table.style = 'Light Grid Accent 1'
        except Exception:
            pass
        for i, header in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = header
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True
        for r, (label, stats) in enumerate(item_stats.items(), start=1):
            d = getattr(stats, 'decimals', 1)
            values = [label, str(stats.n), f"{stats.mean:.{d}f}", f"{stats.median:.{d}f}", f"{stats.std:.{d}f}",
                      f"{stats.min_value:.{d}f}", f"{stats.max_value:.{d}f}"]
            if constant_sum:
                values.append(f"{shares.get(label, 0):.{d}f} %")
            for c, value in enumerate(values):
                table.rows[r].cells[c].text = value
        if constant_sum:
            note = doc.add_paragraph()
            run = note.add_run(_fixed_text(_word("constant_sum_note", share=f"{constant_sum['share_matching']:.0f}",
                                                 n=constant_sum['n_respondents'], total=f"{constant_sum['total']:g}"),
                                           lang))
            run.italic = True
            run.font.size = Pt(9)
        doc.add_paragraph()

    def _add_numeric_stats_table(
        self,
        doc,
        numeric_stats,
        lang: str = 'de',
    ):
        """
        Renders a compact statistics table for numeric results.
        
        Format:
            | n | Mean | Median | SD  | Min | Max |
            | 47 | 4.2 | 3.0    | 5.1 | 0   | 28  |
        
        Args:
            doc: Word document.
            numeric_stats: NumericResult object (handlers/numeric).
                Nothing is rendered if None or without n>0.
            lang: language of the header labels.
        """
        if numeric_stats is None or getattr(numeric_stats, 'n', 0) == 0:
            return
        
        # multilingual headers
        headers_by_lang = _word("numeric_headers")
        headers = _fixed_text(headers_by_lang, lang)
        
        # format the values
        decimals = getattr(numeric_stats, 'decimals', 1)
        unit = getattr(numeric_stats, 'unit', '')
        
        def fmt(v):
            if v is None:
                return '—'
            try:
                return f"{v:.{decimals}f}"
            except (TypeError, ValueError):
                return str(v)
        
        values = [
            str(numeric_stats.n),
            fmt(numeric_stats.mean),
            fmt(numeric_stats.median),
            fmt(numeric_stats.std),
            fmt(numeric_stats.min_value),
            fmt(numeric_stats.max_value),
        ]
        
        # create the table: 2 rows x 6 columns
        table = doc.add_table(rows=2, cols=6)
        try:
            table.style = 'Light Grid Accent 1'
        except Exception:
            # ignore if the style is not available
            pass
        
        # header row
        for i, header in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = header
            # header in bold
            for para in cell.paragraphs:
                for run in para.runs:
                    run.bold = True
        
        # data row
        for i, val in enumerate(values):
            table.rows[1].cells[i].text = val
        
        # optional caption with the unit
        if unit:
            note = doc.add_paragraph()
            note_run = note.add_run(f"({unit})")
            note_run.italic = True
            note_run.font.size = Pt(9)
        
        # note on many invalid values
        if numeric_stats.n_invalid > 0:
            invalid_msg_by_lang = _word("invalid_excluded", count=numeric_stats.n_invalid)
            note_msg = _fixed_text(invalid_msg_by_lang, lang)
            note = doc.add_paragraph()
            note_run = note.add_run(note_msg)
            note_run.italic = True
            note_run.font.size = Pt(9)
        
        # spacing after the table
        doc.add_paragraph()
    
    def _add_segment_table(
        self,
        doc: Document,
        segment_value_data: list,
        total_n: int,
        lang: str,
        dimension: str = None
    ):
        """
        Adds a distribution table of the source question of a segment dimension.
        
        Args:
            doc: Word document
            segment_value_data: list of tuples [(value, count), ...]
            total_n: total number of answers
            lang: language
            dimension: dimension id (for the labels of the dimension and its values)
        """
        if not segment_value_data:
            return
        from export.segment_context import get_context
        dim_label = dimension or ''
        if dimension:
            try:
                dim_label = get_context().label(dimension).get(lang, dimension)
            except Exception:
                pass
        
        doc.add_paragraph()  # spacing
        para = doc.add_paragraph()
        run = para.add_run(text("report.distribution.title", lang, dimension=dim_label))
        run.bold = True
        
        header_row = [dim_label, text("report.distribution.count", lang),
                      text("report.distribution.share", lang)]
        
        # create the table
        table = doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        
        # Header
        header_cells = table.rows[0].cells
        for i, header_text in enumerate(header_row):
            header_cells[i].text = header_text
            for paragraph in header_cells[i].paragraphs:
                for run in paragraph.runs:
                    run.bold = True
        
        # data rows
        for segment_value_name, count in segment_value_data:
            row_cells = table.add_row().cells
            display_name = self.tm.get_segment_label(dimension, str(segment_value_name), lang) if dimension else str(segment_value_name)
            row_cells[0].text = display_name
            row_cells[1].text = str(count)
            percentage = (count / total_n * 100) if total_n > 0 else 0
            row_cells[2].text = f"{percentage:.1f}%"
        
        # adjust the column widths
        for cell in table.columns[0].cells:
            cell.width = Cm(6)
        for cell in table.columns[1].cells:
            cell.width = Cm(2)
        for cell in table.columns[2].cells:
            cell.width = Cm(2)
        
        doc.add_paragraph()  # spacing after the table
    
    def export_full_report(
        self,
        results: list[dict],
        filename: str = "survey_report",
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de",
        report_groups: dict = None,
        config: dict = None
    ) -> Path:
        """
        Creates and saves a complete report.
        
        Args:
            results: list of result dictionaries
            filename: file name (without extension)
            title: report title
            subtitle: subtitle
            lang: language
            report_groups: dictionary with the question groups of the configuration
            config: complete configuration for additional information
        
        Returns:
            path of the saved file
        """
        doc = self.create_full_report(
            results=results,
            title=title,
            subtitle=subtitle,
            lang=lang,
            report_groups=report_groups,
            config=config
        )
        
        filepath = self.output_dir / f"{filename}_{lang}.docx"
        return self.save_document(doc, filepath)
    
    def create_segmented_report(
        self,
        all_results: dict,
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de",
        config: dict = None
    ) -> Document:
        """
        Creates a report with the overall results.
        
        Structure:
        1. title page
        2. table of contents
        3. part 1: all overall results
        4. part 2: themes per segment dimension with segment-specific clusters (free text)
        
        Args:
            all_results: dict as returned by analyze_all_questions_with_segments()
            title: report title
            subtitle: subtitle
            lang: language
            config: configuration with question_groups (for the charts)
        
        Returns:
            Word document
        """
        from datetime import datetime
        from handlers import get_handler
        
        doc = self._create_document(lang, title=title, subtitle=subtitle)
        
        # === TITLE PAGE ===
        title_para = doc.add_paragraph()
        title_run = title_para.add_run(title)
        title_run.bold = True
        title_run.font.size = Pt(28)
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_para.space_after = Pt(12)
        
        if subtitle:
            subtitle_para = doc.add_paragraph()
            subtitle_run = subtitle_para.add_run(subtitle)
            subtitle_run.font.size = Pt(16)
            subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # date
        date_para = doc.add_paragraph()
        date_str = datetime.now().strftime("%d.%m.%Y")
        date_labels = _word("generated_on", date=date_str)
        date_run = date_para.add_run(_fixed_text(date_labels, lang))
        date_run.font.size = Pt(11)
        date_run.italic = True
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_page_break()
        
        # === LIST OF FIGURES ===
        self._add_table_of_figures(doc, lang)
        
        # === PART 1: OVERALL RESULTS ===
        part1_titles = _word("part1_overall")
        doc.add_heading(_fixed_text(part1_titles, lang), level=1)
        
        # question configs from the configuration
        question_groups = config.get('question_groups', {}) if config else {}
        
        for qid, q_data in all_results.items():
            if 'error' in q_data:
                continue
            
            question_text = q_data.get('question_text', qid)
            total_result = q_data.get('total')
            
            if total_result is None:
                continue
            
            # question as heading
            doc.add_heading(question_text, level=2)
            
            # create the overall chart
            handler_type = q_data.get('handler_type', 'single_choice')
            qconfig = question_groups.get(qid, {})
            
            # objects as well as dicts
            if hasattr(total_result, 'n'):
                n = total_result.n
                data = total_result.data
                metadata = total_result.metadata if hasattr(total_result, 'metadata') else {}
            else:
                # dict from the cache/database
                n = total_result.get('n')
                data = total_result.get('data')
                metadata = total_result.get('metadata', {})
            
            try:
                handler = get_handler(handler_type, qid, qconfig)
                if handler and not _is_empty(data):
                    # an AnalysisResult-like object for the handler
                    from handlers.base import AnalysisResult
                    result_obj = AnalysisResult(
                        question_id=qid,
                        n=n or 0,
                        data=data,
                        percentages=total_result.percentages if hasattr(total_result, 'percentages') else total_result.get('percentages', {}),
                        description=total_result.description if hasattr(total_result, 'description') else total_result.get('description', ''),
                        warnings=total_result.warnings if hasattr(total_result, 'warnings') else total_result.get('warnings', []),
                        clusters=total_result.clusters if hasattr(total_result, 'clusters') else total_result.get('clusters', []),
                        metadata=metadata
                    )
                    
                    default_chart = qconfig.get('default_chart', 'bar_horizontal')
                    fig = handler.create_chart(result_obj, default_chart, lang=lang)
                    if fig:
                        self._add_figure_with_caption(
                            doc=doc,
                            fig=fig,
                            caption_text=question_text,
                            n=n,
                            width=5.5,
                            lang=lang
                        )
            except Exception as e:
                logger.warning(f"Chart creation failed for {qid}: {e}")
            
            # cluster details for free text
            if handler_type == 'freetext':
                cluster_details = metadata.get('cluster_details', [])
                
                if cluster_details:
                    self.add_cluster_details_section(doc, cluster_details, lang)
        
        doc.add_page_break()
        
        # === PART 2: SEGMENT-SPECIFIC THEMES (for free text) ===
        # are there free-text questions?
        has_freetext = any(
            q_data.get('handler_type') == 'freetext' 
            for q_data in all_results.values() 
            if 'error' not in q_data
        )
        
        if has_freetext:
            # Part 2: segment-specific themes, one part per dimension that
            # enables them (enable_specific_clusters), only for segments that
            # reach specific_clusters_min_n.
            from export.segment_context import get_context
            ctx = get_context()
            dims = [d for d in ctx.dimension_ids()
                    if ctx.resolver.specific_clusters_threshold(d) is not None]
            for dim in dims:
                threshold = ctx.resolver.specific_clusters_threshold(dim)
                dim_label = ctx.label(dim).get(lang, dim)
                part_started = False
                for qid, q_data in all_results.items():
                    if 'error' in q_data or q_data.get('handler_type') != 'freetext':
                        continue
                    segments = q_data.get('by_segment', {}).get(dim, {}).get('segments', {})
                    segments = {k: v for k, v in segments.items()
                                if v.get('result') is not None and v.get('n', 0) >= threshold}
                    if not segments:
                        continue
                    if not part_started:
                        doc.add_heading(text("report.segment_themes.part_title", lang,
                                             dimension=dim_label), level=1)
                        part_started = True
                    doc.add_heading(q_data.get('question_text', qid), level=2)
                    self._add_segment_theme_details(doc, dim, segments, lang)
        
        return doc
    
    def _add_segment_theme_details(self, doc: Document, dim: str, segments: dict, lang: str):
        """Cluster details of each segment of one free-text question."""
        for seg_value, seg_data in segments.items():
            result = seg_data.get('result')
            n = seg_data.get('n', 0)
            warning = seg_data.get('warning', '')
            
            heading = self.tm.get_segment_label(dim, seg_value, lang)
            heading += f" (n={n}, {warning})" if warning else f" (n={n})"
            doc.add_heading(heading, level=3)
            
            # Cluster-Details
            metadata = result.metadata if hasattr(result, 'metadata') else {}
            cluster_details = metadata.get('cluster_details', [])
            
            if cluster_details:
                for cluster in cluster_details[:5]:  # Top 5 Cluster
                    count = cluster.get('count', 0)
                    para = doc.add_paragraph()
                    # name and description may be {lang: text}: in the report language
                    name = _config_text(cluster.get('name'), lang, '')
                    description = _config_text(cluster.get('description'), lang, '')
                    run = para.add_run(f"• {name} ({count})")
                    run.bold = True
                    
                    if description:
                        doc.add_paragraph(f"  {description}")
            else:
                # simple table without clusters
                if hasattr(result, 'data') and isinstance(result.data, dict):
                    self.add_simple_table(
                        doc=doc,
                        data=result.data,
                        percentages=result.percentages if hasattr(result, 'percentages') else {},
                        n=n,
                        warning=warning,
                        lang=lang
                    )

    def export_segmented_report(
        self,
        all_results: dict,
        filename: str = "survey_report",
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de",
        config: dict = None
    ) -> Path:
        """
        Creates and saves a segmented report.
        
        Args:
            all_results: results of analyze_all_questions_with_segments()
            filename: file name
            title: title
            subtitle: subtitle
            lang: language
            config: configuration with question_groups (for the charts)
        
        Returns:
            file path
        """
        doc = self.create_segmented_report(
            all_results=all_results,
            title=title,
            subtitle=subtitle,
            lang=lang,
            config=config
        )
        
        filepath = self.output_dir / f"{filename}_{lang}.docx"
        return self.save_document(doc, filepath)
    
    def export_multilingual(
        self,
        question_id: str,
        question_texts: dict[str, str],
        create_fig_func: callable,
        description_texts: dict[str, str],
        n: int,
        output_dir: Path,
        langs: list[str] = None,
        additional_content: Optional[dict] = None
    ) -> dict[str, Path]:
        """
        Exports a document in several languages.
        
        Args:
            question_id: id of the question
            question_texts: {lang: question text}
            create_fig_func: function that creates the figure for a language
            description_texts: {lang: description}
            n: sample size
            output_dir: output directory
            langs: languages (default: the output languages of the survey)
            additional_content: additional content
        
        Returns:
            dictionary {lang: file path}
        """
        if langs is None:
            from .languages import get_output_languages
            from .prompts import current_config
            langs = get_output_languages(current_config())
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        saved_files = {}
        
        for lang in langs:
            question_text = _config_text(question_texts, lang, "")
            description = _config_text(description_texts, lang, "")
            
            # create the figure for this language
            fig = create_fig_func(lang)
            
            # create the document
            doc = self.create_question_document(
                question_id=question_id,
                question_text=question_text,
                fig=fig,
                description=description,
                n=n,
                lang=lang,
                additional_content=additional_content
            )
            
            # save
            filepath = output_dir / f"{question_id}_{lang}.docx"
            self.save_document(doc, filepath)
            saved_files[lang] = filepath
        
        return saved_files


    def create_complete_report(
        self,
        analyses: list,  # List[QuestionAnalysisComplete]
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de"
    ) -> Document:
        """
        Creates a complete report.
        
        Structure per question:
        1. overall result (chart + table)
        2. (free text) cluster details
        3. (free text) segment-specific themes
        
        Args:
            analyses: list of QuestionAnalysisComplete objects
            title: report title
            subtitle: subtitle
            lang: language
        
        Returns:
            Word document
        """
        from datetime import datetime
        
        doc = self._create_document(lang, title=title, subtitle=subtitle)
        
        # === TITLE PAGE ===
        title_para = doc.add_paragraph()
        title_run = title_para.add_run(title)
        title_run.bold = True
        title_run.font.size = Pt(28)
        title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_para.space_after = Pt(12)
        
        if subtitle:
            subtitle_para = doc.add_paragraph()
            subtitle_run = subtitle_para.add_run(subtitle)
            subtitle_run.font.size = Pt(16)
            subtitle_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # date
        date_para = doc.add_paragraph()
        date_str = datetime.now().strftime("%d.%m.%Y")
        date_labels = _word("generated_on", date=date_str)
        date_run = date_para.add_run(_fixed_text(date_labels, lang))
        date_run.font.size = Pt(11)
        date_run.italic = True
        date_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        # number of questions
        count_para = doc.add_paragraph()
        count_texts = _word("analyses_count", count=len(analyses))
        count_run = count_para.add_run(_fixed_text(count_texts, lang))
        count_run.font.size = Pt(11)
        count_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        
        doc.add_page_break()
        
        # === TABLE OF CONTENTS ===
        toc_titles = _word("toc")
        toc_para = doc.add_paragraph(_fixed_text(toc_titles, lang))
        toc_para.style = doc.styles['TOC Heading']
        
        # a real Word TOC field
        para = doc.add_paragraph()
        fld_simple = OxmlElement('w:fldSimple')
        fld_simple.set(qn('w:instr'), 'TOC \\o "1-3" \\h \\z \\u')
        
        run_element = OxmlElement('w:r')
        text_element = OxmlElement('w:t')
        placeholder_labels = _word("toc_hint")
        text_element.text = _fixed_text(placeholder_labels, lang)
        run_element.append(text_element)
        fld_simple.append(run_element)
        para._p.append(fld_simple)
        
        doc.add_page_break()
        
        # === SINGLE RESULTS ===
        for i, analysis in enumerate(analyses):
            self._add_question_analysis(doc, analysis, lang)
            
            # page break (except after the last question)
            if i < len(analyses) - 1:
                doc.add_page_break()
        
        return doc
    
    def _add_question_analysis(
        self,
        doc: Document,
        analysis,  # QuestionAnalysisComplete
        lang: str
    ):
        """Adds a complete question analysis to the document."""
        
        # === QUESTION HEADING ===
        doc.add_heading(analysis.question_text, level=1)
        
        # === OVERALL RESULT ===
        gesamt_titles = _word("overall")
        doc.add_heading(_fixed_text(gesamt_titles, lang), level=2)
        
        # n-Info
        result = analysis.total_result
        metadata = result.metadata if hasattr(result, 'metadata') else {}
        
        if analysis.handler_type == 'freetext' and metadata.get('analysis_mode') in ('items', 'fields'):
            n_items = metadata.get('total_items', result.n)
            n_responses = metadata.get('unique_responses', result.n)
            if n_items != n_responses:
                n_texts = _texts("handler.freetext.chart_items", n_items=n_items, n_responses=n_responses)
                n_info = _fixed_text(n_texts, lang)
            else:
                n_info = f"n = {n_responses}"
        else:
            n_info = f"n = {result.n}"
        
        n_para = doc.add_paragraph(n_info)
        n_para.runs[0].font.italic = True
        n_para.runs[0].font.size = Pt(9)
        
        # Chart
        if analysis.total_chart:
            self._add_figure(doc, analysis.total_chart, width=5.5)
        
        # simple table for the total
        if hasattr(result, 'data') and isinstance(result.data, dict) and result.data:
            self.add_simple_table(
                doc=doc,
                data=result.data,
                percentages=result.percentages if hasattr(result, 'percentages') else {},
                n=result.n,
                lang=lang
            )
        
        # === FREE TEXT ===
        if analysis.handler_type == 'freetext':
            # Cluster-Details
            cluster_details = metadata.get('cluster_details', [])
            if cluster_details:
                cluster_titles = _word("theme_clusters")
                doc.add_heading(_fixed_text(cluster_titles, lang), level=2)
                
                for cluster in cluster_details:
                    c_name = cluster.get('name', '')
                    if isinstance(c_name, dict):
                        c_name = _config_text(c_name, lang, str(c_name))
                    count = cluster.get('count', 0)
                    unique_resp = cluster.get('unique_respondents', 0)
                    heading_text = _cluster_heading_text(c_name, count, unique_resp, lang)
                    doc.add_heading(heading_text, level=3)
                    
                    if cluster.get('description'):
                        doc.add_paragraph(cluster['description'])
                    
                    if cluster.get('examples'):
                        example_labels = _word("examples")
                        doc.add_paragraph(_fixed_text(example_labels, lang))
                        for ex in cluster['examples'][:3]:
                            para = doc.add_paragraph(style='List Bullet')
                            run = para.add_run(f'"{ex}"')
                            run.italic = True
            
            # cluster distribution per segment
            for cluster_comp in analysis.cluster_comparisons:
                doc.add_heading(_text("word.cluster_distribution", lang, segment=cluster_comp.segment_label), level=2)
                
                matrix = cluster_comp.to_matrix()
                if len(matrix) > 1:
                    self._add_matrix_table(doc, matrix)
            
            # segment-specific themes
            if analysis.segment_specific_clusters:
                from export.segment_context import get_context
                ctx = get_context()
                last_dim = None
                for seg_result in analysis.segment_specific_clusters:
                    # One heading per dimension ("Themes by Region")
                    if seg_result.segment_type != last_dim:
                        last_dim = seg_result.segment_type
                        dim_label = (ctx.label(last_dim).get(lang, last_dim)
                                     if last_dim in ctx.dimension_ids() else last_dim)
                        doc.add_heading(text("report.segment_themes.question_title", lang,
                                             dimension=dim_label), level=2)
                    segment_heading = self.tm.get_segment_label(
                        seg_result.segment_type, seg_result.segment_value, lang
                    )
                    mentions = text("report.segment_themes.mentions", lang, n=seg_result.n_items)
                    if seg_result.warning:
                        segment_heading += f" ({mentions}, {seg_result.warning})"
                    else:
                        segment_heading += f" ({mentions}, n={seg_result.n_responses})"
                    
                    doc.add_heading(segment_heading, level=3)
                    
                    # top clusters for this segment
                    for cluster in seg_result.clusters[:5]:
                        count = cluster.get('count', 0)
                        para = doc.add_paragraph()
                        run = para.add_run(f"• {cluster['name']} ({count})")
                        run.bold = True
                        
                        if cluster.get('description'):
                            doc.add_paragraph(f"  {cluster['description']}")
    
    def _convert_comparison_table(self, comp_table) -> dict:
        """Converts a ComparisonTable into a comparison_data dict."""
        columns = []
        for seg in comp_table.segments:
            columns.append({
                'name': seg.segment_value,
                'n': seg.n,
                'warning': seg.warning
            })
        
        rows = []
        for row_label in comp_table.row_labels:
            values = []
            for seg in comp_table.segments:
                count = seg.data.get(row_label, 0) if seg.data else 0
                pct = seg.percentages.get(row_label, 0) if seg.percentages else 0
                values.append({
                    'count': count,
                    'percent': round(pct, 0)
                })
            rows.append({
                'label': row_label,
                'values': values
            })
        
        return {'columns': columns, 'rows': rows}
    
    def _add_matrix_table(self, doc: Document, matrix: list[list[str]]):
        """Adds a simple matrix table."""
        from docx.enum.table import WD_TABLE_ALIGNMENT
        from docx.oxml.ns import nsdecls
        from docx.oxml import parse_xml
        
        if not matrix or not matrix[0]:
            return
        
        n_rows = len(matrix)
        n_cols = len(matrix[0])
        
        table = doc.add_table(rows=n_rows, cols=n_cols)
        table.style = 'Table Grid'
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        
        # column widths: first column (label) wider, the rest evenly
        if n_cols >= 2:
            total_width_cm = 15
            first_col_width = Cm(6)
            remaining_width = total_width_cm - 6
            other_col_width = Cm(remaining_width / (n_cols - 1))
            widths = [first_col_width] + [other_col_width] * (n_cols - 1)
            self._set_column_widths(table, widths)
        
        for row_idx, row_data in enumerate(matrix):
            table_row = table.rows[row_idx]
            for col_idx, cell_text in enumerate(row_data):
                cell = table_row.cells[col_idx]
                cell.text = str(cell_text)
                
                for paragraph in cell.paragraphs:
                    # first column left-aligned, the rest centred
                    if col_idx == 0:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    else:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.font.size = Pt(9)
                        if row_idx == 0:  # Header
                            run.bold = True
                
                # header background
                if row_idx == 0:
                    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="E7E6E6"/>')
                    cell._tc.get_or_add_tcPr().append(shading)
        
        doc.add_paragraph()
    
    def export_complete_report(
        self,
        analyses: list,  # List[QuestionAnalysisComplete]
        filename: str = "survey_complete",
        title: str = "Umfrage-Auswertung",
        subtitle: str = "",
        lang: str = "de"
    ) -> Path:
        """
        Creates and saves a complete report.
        
        Args:
            analyses: list of QuestionAnalysisComplete objects
            filename: file name
            title: title
            subtitle: subtitle
            lang: language
        
        Returns:
            file path
        """
        doc = self.create_complete_report(
            analyses=analyses,
            title=title,
            subtitle=subtitle,
            lang=lang
        )
        
        filepath = self.output_dir / f"{filename}_{lang}.docx"
        return self.save_document(doc, filepath)
    
    # === CORRELATION EXPORT METHODS ===
    
    def add_correlation_section(self, doc: Document, report, config: dict, lang: str = "de"):
        """
        Statistical associations (utils/correlation.CorrelationReport).

        Each question pair appears once: in its theme, otherwise among the
        cross-theme associations. Every finding shows its facts (test, effect,
        adjusted p, n, pattern), the description, and the possible reading
        set apart as an interpretation.
        """
        from utils.languages import get_source_language, localized, text
        src = get_source_language(config)
        doc.add_page_break()
        doc.add_heading(text("correlations.report.title", lang), level=1)
        doc.add_paragraph(text("correlations.report.intro", lang))
        shown = set()
        for summary in report.themes.values():
            pairs = [p for p in summary.pairs if p.key not in shown]
            if not pairs:
                continue
            shown.update(p.key for p in pairs)
            doc.add_heading(str(localized(summary.theme_label, lang, src) or summary.theme_id), level=2)
            doc.add_heading(text("correlations.report.strongest", lang), level=4)
            for pair in pairs:
                self._add_correlation_box(doc, pair, src, lang)
            if summary.theme_insight.get(lang):
                doc.add_heading(text("correlations.report.summary", lang), level=4)
                doc.add_paragraph().add_run(summary.theme_insight[lang]).italic = True
        cross = [(p, t1, t2) for p, t1, t2 in report.cross if p.key not in shown]
        if cross:
            doc.add_heading(text("correlations.cross_theme", lang), level=2)
            for pair, t1, t2 in cross:
                themes = " / ".join(str(localized(report.theme_labels.get(t, {src: t}), lang, src)) for t in (t1, t2))
                self._add_correlation_box(doc, pair, src, lang, theme_info=themes)

    def _add_correlation_box(self, doc: Document, pair, src: str, lang: str, theme_info: str = None):
        """One question pair with its findings."""
        from utils.correlation import finding_facts, variable_label
        from utils.languages import localized, text
        header = doc.add_paragraph()
        header.add_run(str(localized(pair.question_1_label, lang, src))).bold = True
        header.add_run("  –  ")
        header.add_run(str(localized(pair.question_2_label, lang, src))).bold = True
        if theme_info:
            run = doc.add_paragraph().add_run(f"{text('correlations.report.themes', lang)}: {theme_info}")
            run.italic = True
            run.font.size = Pt(10)
        for finding in pair.findings:
            r = finding.result
            title = doc.add_paragraph()
            title.add_run(f"{variable_label(r.var_a, lang, src)}  ↔  {variable_label(r.var_b, lang, src)}").bold = True
            for fact in finding_facts(r, lang, src):
                para = doc.add_paragraph(style='List Bullet')
                para.add_run(fact).font.size = Pt(10)
            if finding.description.get(lang):
                doc.add_paragraph(finding.description[lang])
            if finding.reading.get(lang):
                para = doc.add_paragraph()
                para.add_run(f"{text('analysis.reading', lang)}: ").bold = True
                para.add_run(finding.reading[lang]).italic = True
        doc.add_paragraph()

    def add_survey_overview_section(
        self,
        doc: Document,
        config: dict,
        lang: str = "de"
    ):
        """
        Adds an overview of the questions at the end of the document.
        
        For every question:
        - question text
        - question type
        - answer options (if any)
        
        Grouped by report_groups in the configured order.
        
        Args:
            doc: Word document
            config: complete configuration (with question_groups and settings)
            lang: language of the texts
        """
        from docx.shared import RGBColor
        
        # headings per language
        section_titles = _word("questions_overview")
        
        intro_texts = _word("questions_overview_intro")
        
        type_labels = _word("question_types")
        
        options_label = _word("options")
        
        type_label_prefix = _word("type")
        
        scale_label = _word("scale")
        
        rows_label = _word("rows")
        
        # page break before the appendix
        doc.add_page_break()
        
        # main heading
        heading = doc.add_heading(_fixed_text(section_titles, lang), level=1)
        
        # introduction
        intro = doc.add_paragraph(_fixed_text(intro_texts, lang))
        intro.paragraph_format.space_after = Pt(12)
        
        # load and sort the report groups
        report_groups = config.get('settings', {}).get('report_groups', {})
        question_groups = config.get('question_groups', {})
        
        # sort by order
        sorted_groups = sorted(
            report_groups.items(),
            key=lambda x: x[1].get('order', 999)
        )
        
        question_counter = 1
        
        for group_id, group_config in sorted_groups:
            group_label = _config_text(group_config.get('label', {}), lang, group_id)
            group_questions = group_config.get('questions', [])
            
            if not group_questions:
                continue
            
            # group heading (H4: below the TOC levels 1-3)
            group_heading = doc.add_heading(group_label, level=4)
            group_heading.paragraph_format.space_before = Pt(18)
            
            for qid in group_questions:
                qconfig = question_groups.get(qid, {})
                if not qconfig:
                    continue
                
                # Fragetext
                question_text = _config_text(qconfig.get('title', qconfig.get('label', {})), lang, qid)
                
                # Fragetyp
                handler_type = qconfig.get('handler', qconfig.get('type', 'single_choice'))
                type_labels_lang = _fixed_text(type_labels, lang)
                type_text = type_labels_lang.get(handler_type, handler_type)
                
                # question paragraph with numbering
                q_para = doc.add_paragraph()
                q_para.paragraph_format.space_before = Pt(12)
                
                # number and question text (bold)
                q_run = q_para.add_run(f"{question_counter}. {question_text}")
                q_run.bold = True
                
                # type line
                type_para = doc.add_paragraph()
                type_para.paragraph_format.left_indent = Cm(0.5)
                type_prefix = type_label_prefix.get(lang, "Type")
                type_run = type_para.add_run(f"{type_prefix}: ")
                type_run.italic = True
                type_para.add_run(type_text)
                
                # options (depending on the question type)
                if handler_type in ['single_choice', 'multi_choice', 'multi_choice_binary']:
                    options = qconfig.get('options', [])
                    # options may also be a dict with language keys
                    if isinstance(options, dict):
                        options = _config_text(options, lang, [])
                    
                    if options:
                        opt_para = doc.add_paragraph()
                        opt_para.paragraph_format.left_indent = Cm(0.5)
                        opt_label = options_label.get(lang, "Options")
                        opt_run = opt_para.add_run(f"{opt_label}: ")
                        opt_run.italic = True
                        
                        # options as a comma-separated list
                        if len(options) <= 5:
                            opt_para.add_run(", ".join(str(o) for o in options))
                        else:
                            # many options: a list
                            opt_para.add_run("")
                            for opt in options:
                                opt_item = doc.add_paragraph(f"• {opt}")
                                opt_item.paragraph_format.left_indent = Cm(1.0)
                                opt_item.paragraph_format.space_before = Pt(0)
                                opt_item.paragraph_format.space_after = Pt(0)
                
                elif handler_type == 'matrix_likert':
                    # show the scale
                    matrix_scale = qconfig.get('scale', [])
                    if isinstance(matrix_scale, dict):
                        matrix_scale = _config_text(matrix_scale, lang, [])
                    
                    if matrix_scale:
                        scale_para = doc.add_paragraph()
                        scale_para.paragraph_format.left_indent = Cm(0.5)
                        sc_label = scale_label.get(lang, "Scale")
                        sc_run = scale_para.add_run(f"{sc_label}: ")
                        sc_run.italic = True
                        scale_para.add_run(", ".join(str(s) for s in matrix_scale))
                    
                    # show the rows (rows or row_labels)
                    matrix_rows = qconfig.get('rows', qconfig.get('row_labels', []))
                    if isinstance(matrix_rows, dict):
                        # rows may be {row_id: {de: ..., fr: ..., en: ...}} or {de: [...], fr: [...]}
                        if lang in matrix_rows and isinstance(matrix_rows[lang], list):
                            matrix_rows = matrix_rows[lang]
                        elif isinstance(_config_text(matrix_rows, lang, None), list):
                            matrix_rows = _config_text(matrix_rows, lang, None)
                        else:
                            matrix_rows = [_config_text(v, lang, k) if isinstance(v, dict) else v 
                                          for k, v in matrix_rows.items()]
                    
                    if matrix_rows:
                        rows_para = doc.add_paragraph()
                        rows_para.paragraph_format.left_indent = Cm(0.5)
                        r_label = rows_label.get(lang, "Rows")
                        r_run = rows_para.add_run(f"{r_label}: ")
                        r_run.italic = True
                        
                        if len(matrix_rows) <= 4:
                            rows_para.add_run(", ".join(str(r) for r in matrix_rows))
                        else:
                            rows_para.add_run("")
                            for row in matrix_rows:
                                row_item = doc.add_paragraph(f"• {row}")
                                row_item.paragraph_format.left_indent = Cm(1.0)
                                row_item.paragraph_format.space_before = Pt(0)
                                row_item.paragraph_format.space_after = Pt(0)
                
                elif handler_type == 'ranking':
                    options = qconfig.get('options', qconfig.get('items', []))
                    if isinstance(options, dict):
                        options = _config_text(options, lang, [])
                    
                    if options:
                        opt_para = doc.add_paragraph()
                        opt_para.paragraph_format.left_indent = Cm(0.5)
                        opt_label = options_label.get(lang, "Options")
                        opt_run = opt_para.add_run(f"{opt_label}: ")
                        opt_run.italic = True
                        
                        for i, opt in enumerate(options, 1):
                            opt_item = doc.add_paragraph(f"{i}. {opt}")
                            opt_item.paragraph_format.left_indent = Cm(1.0)
                            opt_item.paragraph_format.space_before = Pt(0)
                            opt_item.paragraph_format.space_after = Pt(0)
                
                question_counter += 1
        
        # closing empty line
        doc.add_paragraph()

    def add_segment_comparison_section(
        self,
        doc: Document,
        segment_clusters_data: dict,
        lang: str = "de"
    ):
        """
        Segment-specific topics of one dimension (export/insights.py,
        segment_clusters.json): per free-text question and group the topics
        with their counted shares.

        Descriptive only: every group is clustered on its own, so the topics
        of two groups are not tested against each other. Tested differences
        between groups are in add_group_differences_section.
        """
        from utils.languages import localized
        if not segment_clusters_data or not segment_clusters_data.get('questions'):
            return
        labels = segment_clusters_data.get('dimension_label', {})
        dimension = str(localized(labels, lang, lang) or '') if isinstance(labels, dict) else str(labels)
        doc.add_page_break()
        doc.add_heading(text("report.segment_comparison.title", lang, dimension=dimension), level=1)
        doc.add_paragraph(text("report.segment_comparison.intro", lang, dimension=dimension))
        for qid, question_data in segment_clusters_data.get('questions', {}).items():
            groups = question_data.get('clusters_by_value', {})
            if not groups:
                continue
            doc.add_heading(str(localized(question_data.get('question_label', {}), lang, lang) or qid), level=4)
            for group in groups.values():
                para = doc.add_paragraph()
                para.add_run(text("report.segment_comparison.group", lang,
                                  group=str(localized(group.get('segment_value_label', {}), lang, lang) or ''),
                                  n=group.get('total_responses', 0))).bold = True
                for cluster in group.get('clusters', []):
                    item = doc.add_paragraph(style='List Bullet')
                    item.add_run(text("report.segment_comparison.topic", lang,
                                      name=str(localized(cluster.get('name', {}), lang, lang) or ''),
                                      share=f"{cluster.get('percentage', 0):.0f}")).font.size = Pt(10)
            doc.add_paragraph()

    def add_group_differences_section(self, doc: Document, report, config: dict, lang: str = "de"):
        """
        Tested differences between groups (utils/segment_findings.SegmentDifferenceReport).

        Per question with at least one listed finding: per dimension the
        findings with their facts, the description, and the possible reading
        set apart as an interpretation, then the summary of the dimension (from
        two findings on, written from the descriptions only); dimensions without a listed finding
        get their fixed sentence. Questions without any difference are named
        in one closing sentence instead of one block each.
        """
        from utils.languages import get_source_language, localized
        src = get_source_language(config)
        doc.add_page_break()
        doc.add_heading(text("report.group_differences.title", lang), level=1)
        doc.add_paragraph(text("report.group_differences.intro", lang, points=f"{report.min_difference:g}"))
        shown = 0
        for qid, question in report.questions.items():
            if not question.findings:
                continue
            shown += 1
            doc.add_heading(str(localized(question.label, lang, src) or qid), level=3)
            for dim in question.dimensions:
                dim_label = str(localized(dim.label, lang, src) or dim.dimension_id)
                if not dim.findings:
                    run = doc.add_paragraph().add_run(dim.statement(lang, src, report.min_difference))
                    run.italic = True
                    run.font.size = Pt(10)
                    continue
                doc.add_heading(text("report.group_differences.by", lang, dimension=dim_label), level=4)
                for finding in dim.findings:
                    self._add_group_difference(doc, finding, src, lang)
                if dim.summary.get(lang):
                    para = doc.add_paragraph()
                    para.add_run(f"{text('report.group_differences.summary', lang)}: ").bold = True
                    para.add_run(dim.summary[lang])
        if not shown:
            doc.add_paragraph(text("report.group_differences.none", lang))

    def _add_group_difference(self, doc: Document, finding, src: str, lang: str):
        from utils.correlation import variable_label
        title = doc.add_paragraph()
        title.add_run(variable_label(finding.result.var_b, lang, src)).bold = True
        for fact in finding.facts(lang, src):
            para = doc.add_paragraph(style='List Bullet')
            para.add_run(fact).font.size = Pt(10)
        if finding.description.get(lang):
            doc.add_paragraph(finding.description[lang])
        if finding.reading.get(lang):
            para = doc.add_paragraph()
            para.add_run(f"{text('analysis.reading', lang)}: ").bold = True
            para.add_run(finding.reading[lang]).italic = True


def export_question_to_word(
    question_id: str,
    question_config: dict,
    analysis_result: dict,
    fig: plt.Figure,
    output_dir: Path,
    lang: str = "de"
) -> Path:
    """
    Convenience function to export one question.
    
    Args:
        question_id: id of the question
        question_config: configuration of the question group
        analysis_result: analysis result
        fig: chart
        output_dir: output directory
        lang: language
    
    Returns:
        path of the saved file
    """
    exporter = WordExporter()
    
    # question text from the configuration
    question_text = question_config.get("label", {}).get(lang, question_id)
    
    # description from the analysis
    description = analysis_result.get("description", "")
    n = analysis_result.get("n", 0)
    
    doc = exporter.create_question_document(
        question_id=question_id,
        question_text=question_text,
        fig=fig,
        description=description,
        n=n,
        lang=lang,
        additional_content=analysis_result.get("additional_content")
    )
    
    filepath = Path(output_dir) / f"{question_id}_{lang}.docx"
    return exporter.save_document(doc, filepath)

def _is_empty(data) -> bool:
    """True for None, an empty dict/list or an empty DataFrame (a DataFrame has no truth value)."""
    if data is None:
        return True
    if hasattr(data, "empty"):
        return bool(data.empty)
    return not data
