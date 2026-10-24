# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Data import module of the survey analysis tool: import functions and helpers
for matching columns.
"""

import logging
import re
from pathlib import Path
from difflib import SequenceMatcher
from typing import List, Tuple, Optional, Set

import pandas as pd
import yaml

from core import state, load_config, DATA_DIR, CONFIG_DIR, get_active_config_dir

logger = logging.getLogger(__name__)


def import_data(file, lss_file=None, sheet_name: str = None) -> Tuple[str, Optional[pd.DataFrame]]:
    """
    Imports a CSV/Excel/SPSS file and optionally a structure file (LSS, QSF).
    
    Args:
        file: uploaded file (Gradio file object)
        lss_file: optional structure file for multilingual question texts
        sheet_name: optional name of the Excel sheet (for Excel files)
    
    Returns:
        tuple (status message, DataFrame or None)
    """
    if file is None:
        return "No file selected", None
    
    try:
        filepath = Path(file.name)
        logger.info(f"Importing the file: {filepath}")
        
        # read the file
        structure_status = ""
        if filepath.suffix.lower() == '.sav':
            # SPSS: data with answer texts plus the survey structure from its labels
            from utils.structure_spss import read_spss
            df, structure = read_spss(str(filepath))
            structure_status = _apply_structure(structure)
        elif filepath.suffix.lower() in ['.xlsx', '.xls']:
            df = _import_excel(filepath, sheet_name)
            from utils.structure_qualtrics import split_qualtrics_header
            df, import_ids, _ = split_qualtrics_header(df)
            if import_ids:
                structure_status = "\nℹ️ Qualtrics export detected: two header rows (question text, ImportId) skipped"
                if lss_file is not None and str(lss_file.name).lower().endswith('.qsf'):
                    from utils.structure_qualtrics import qualtrics_structure, read_qsf
                    df, structure = qualtrics_structure(read_qsf(lss_file.name), df, import_ids)
                    structure_status += _apply_structure(structure)
                    lss_file = None   # handled here
        elif _is_qualtrics_csv(filepath):
            # Qualtrics CSV: rows 2 and 3 are question texts and ImportIds, not answers
            from utils.structure_qualtrics import qualtrics_structure, read_qualtrics_csv
            df, import_ids, _ = read_qualtrics_csv(str(filepath))
            structure_status = "\nℹ️ Qualtrics export detected: two header rows (question text, ImportId) skipped"
            if lss_file is not None and str(lss_file.name).lower().endswith('.qsf'):
                from utils.structure_qualtrics import read_qsf
                df, structure = qualtrics_structure(read_qsf(lss_file.name), df, import_ids)
                structure_status += _apply_structure(structure)
                lss_file = None   # handled here
        else:
            df = _import_csv(filepath)
        
        if df is None:
            return "❌ Could not read the file", None
        
        # LSS file: an empty configuration is generated from it (questions,
        # types, texts in all languages); an existing one is only enriched
        lss_status = ""
        if lss_file is not None and str(lss_file.name).lower().endswith('.qsf'):
            # QSF with data without ImportId row (e.g. Excel): columns by DataExportTag
            from utils.structure_qualtrics import qualtrics_structure, read_qsf
            df, structure = qualtrics_structure(read_qsf(lss_file.name), df, {})
            structure_status += _apply_structure(structure)
            lss_file = None
        if lss_file is not None:
            try:
                df, recode_status = _recode_with_lss(lss_file, df)
                lss_status = recode_status + _process_lss_file(lss_file, list(df.columns))
            except Exception as e:
                logger.warning(f"LSS processing failed: {e}")
                lss_status = f"\n⚠️ LSS import failed: {e}"
        
        # basic validation
        if df.empty:
            return "❌ The file is empty", None

        # no structure file and no configuration yet: a first configuration from the
        # data columns (to be checked: see the notes in the status)
        if lss_file is None and not structure_status and not _active_config().get('question_groups'):
            structure_status = _apply_data_structure(df)
        
        # save in the database
        if state.db is not None and state.project_id is not None:
            # id of the active survey
            try:
                from core import get_active_survey_id
                survey_id = get_active_survey_id() or 'default'
            except Exception:
                survey_id = 'default'
            
            import_id = state.db.save_import(
                project_id=state.project_id,
                df=df,
                filename=filepath.name,
                survey_id=survey_id,
            )
            state.import_id = import_id
        else:
            logger.warning("No database connection, import in memory only")
        
        state.df = df
        state.analysis_results = {}  # Reset
        
        # load the configuration
        load_config()
        
        # validate the columns against the configuration
        validation_msg = _validate_columns(df)
        
        suffix = filepath.suffix.lower()
        file_type = "Excel" if suffix in ['.xlsx', '.xls'] else ("SPSS" if suffix == '.sav' else "CSV")
        
        msg = (
            f"✅ Import successful!\n\n"
            f"📄 File: {filepath.name} ({file_type})\n"
            f"📊 Rows: {len(df)}\n"
            f"📋 Columns: {len(df.columns)}\n"
            f"🔑 Import ID: {state.import_id or 'in memory only'}\n"
            f"{structure_status}{lss_status}"
            f"\n{validation_msg}"
        )
        
        return msg, df
        
    except Exception as e:
        logger.error(f"Import error: {e}", exc_info=True)
        return f"❌ Import error: {e}", None


def _import_excel(filepath: Path, sheet_name: str = None) -> Optional[pd.DataFrame]:
    """
    Imports an Excel file (.xlsx or .xls).
    
    Args:
        filepath: path of the Excel file
        sheet_name: optional name of the sheet (default: the first sheet)
    
    Returns:
        DataFrame, or None on errors
    """
    try:
        # check whether openpyxl/xlrd is installed
        if filepath.suffix.lower() == '.xlsx':
            try:
                import openpyxl
            except ImportError:
                logger.error("openpyxl not installed. Please: pip install openpyxl")
                raise ImportError(
                    "Importing Excel needs 'openpyxl'. "
                    "Install it with: pip install openpyxl"
                )
        elif filepath.suffix.lower() == '.xls':
            try:
                import xlrd
            except ImportError:
                logger.error("xlrd not installed. Please: pip install xlrd")
                raise ImportError(
                    "Importing .xls files needs 'xlrd'. "
                    "Install it with: pip install xlrd"
                )
        
        # sheet names
        excel_file = pd.ExcelFile(filepath)
        available_sheets = excel_file.sheet_names
        logger.info(f"Available sheets: {available_sheets}")
        
        # choose the sheet
        if sheet_name and sheet_name in available_sheets:
            selected_sheet = sheet_name
        elif len(available_sheets) == 1:
            selected_sheet = available_sheets[0]
        else:
            # use the first sheet, but warn if there are several
            selected_sheet = available_sheets[0]
            if len(available_sheets) > 1:
                logger.warning(
                    f"Several sheets found: {available_sheets}. "
                    f"Using the first sheet: '{selected_sheet}'"
                )
        
        df = pd.read_excel(filepath, sheet_name=selected_sheet)
        logger.info(f"Excel imported: sheet '{selected_sheet}', {len(df)} rows")
        
        return df
        
    except ImportError:
        raise
    except Exception as e:
        logger.error(f"Excel import error: {e}", exc_info=True)
        raise


def _import_csv(filepath: Path) -> Optional[pd.DataFrame]:
    """
    Imports a CSV file, detecting the encoding.
    
    Args:
        filepath: path of the CSV file
    
    Returns:
        DataFrame, or None on errors
    """
    # try CSV with several encodings
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'iso-8859-1']
    
    for encoding in encodings:
        try:
            df = pd.read_csv(filepath, encoding=encoding, sep=None, engine='python')
            logger.info(f"CSV imported with the encoding: {encoding}")
            return df
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f"CSV import with {encoding} failed: {e}")
            continue
    
    raise ValueError("Could not detect the file encoding")


def get_excel_sheets(file) -> List[str]:
    """
    The sheet names of an Excel file.
    
    Args:
        file: uploaded file (Gradio file object)
    
    Returns:
        list of the sheet names
    """
    if file is None:
        return []
    
    try:
        filepath = Path(file.name)
        
        if filepath.suffix.lower() not in ['.xlsx', '.xls']:
            return []
        
        excel_file = pd.ExcelFile(filepath)
        return excel_file.sheet_names
        
    except Exception as e:
        logger.error(f"Error reading the sheet names: {e}")
        return []


def _recode_with_lss(lss_file, df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """
    LimeSurvey exports with answer codes ("A1", "Y", "I"): replaced by the
    answer texts of the LSS file (utils/survey_structure.apply_answer_codes),
    so the analysis sees the same values as in an export with full answers.
    """
    from utils.lss_parser import LSSParser
    from utils.structure_lss import lss_structure
    from utils.survey_structure import apply_answer_codes
    survey_data = LSSParser().parse_file(str(Path(lss_file.name)))
    df, recoded = apply_answer_codes(df, lss_structure(survey_data, list(df.columns)))
    if not recoded:
        return df, ""
    return df, (f"\nℹ️ Answer codes converted to answer texts for {len(recoded)} questions: "
                f"{', '.join(recoded[:8])}{' …' if len(recoded) > 8 else ''}")


def _process_lss_file(lss_file, data_columns: Optional[List[str]] = None) -> str:
    """
    Processes a LimeSurvey structure file (LSS) and completes questions.yaml.
    
    Args:
        lss_file: uploaded LSS file (Gradio file object)
    
    Returns:
        status message
    """
    from pathlib import Path
    import re
    
    try:
        from utils.lss_parser import LSSParser
    except ImportError:
        return "\n⚠️ LSS parser not available"
    
    lss_path = Path(lss_file.name)
    logger.info(f"Processing the LSS file: {lss_path}")
    
    # parse the LSS
    parser = LSSParser()
    survey_data = parser.parse_file(str(lss_path))
    
    if not survey_data.questions:
        return "\n⚠️ No questions found in the LSS file"
    
    # load the current configuration of the active survey (the same file
    # that is written further below)
    config_path = get_active_config_dir() / "questions.yaml"
    config = {}
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f) or {}
    
    question_groups = config.get('question_groups', {})
    if not question_groups and data_columns is not None:
        return _write_generated_config(survey_data, data_columns, config, config_path)
    if not config_path.exists():
        return "\n⚠️ questions.yaml not found"
    
    # mapping LSS title -> configuration id
    # strategy: compare the question text in the source language with the configured columns
    # IMPORTANT: compare a long prefix to tell similar questions apart
    mapping = {}
    used_lss_titles = set()  # avoid double assignments
    
    for config_id, config_q in question_groups.items():
        columns = config_q.get('columns', [])
        if not columns:
            continue
        
        # use the full column text (up to 120 characters, to tell questions apart)
        col_text = columns[0][:120].lower().strip() if columns else ""
        col_text = re.sub(r'\s+', ' ', col_text)
        
        best_match = None
        best_match_len = 0
        
        for qid, lss_q in survey_data.questions_by_qid.items():
            # skip LSS questions already assigned
            if lss_q.title in used_lss_titles:
                continue
                
            # question text of the LSS file in the survey's source language
            from utils.languages import localized
            from utils.prompts import source_language
            lss_de_text = str(localized(lss_q.texts.question, source_language(), source_language(),
                                        default='') or '')[:120].lower().strip()
            lss_de_text = re.sub(r'\s+', ' ', lss_de_text)
            
            if not col_text or not lss_de_text:
                continue
            
            # find the longest common prefix
            match_len = 0
            for i in range(min(len(col_text), len(lss_de_text))):
                if col_text[i] == lss_de_text[i]:
                    match_len += 1
                else:
                    break
            
            # at least 50 characters must match AND the match must be better than
            # the matches so far
            if match_len >= 50 and match_len > best_match_len:
                best_match = lss_q
                best_match_len = match_len
        
        if best_match:
            mapping[best_match.title] = config_id
            used_lss_titles.add(best_match.title)
            logger.debug(f"LSS mapping: '{config_id}' -> '{best_match.title}' (match: {best_match_len} characters)")
    
    if not mapping:
        return f"\n⚠️ LSS: no question could be mapped (the LSS has {len(survey_data.questions)} questions)"
    
    # complete the configuration
    updated_count = 0
    for lss_title, config_id in mapping.items():
        lss_q = survey_data.questions.get(lss_title)
        if not lss_q or config_id not in question_groups:
            continue
        
        config_q = question_groups[config_id]
        
        # add full_question only if missing or empty: prevents overwriting correct
        # data after a wrong mapping
        if lss_q.texts.question:
            existing_full_q = config_q.get('full_question', {})
            from utils.prompts import source_language
            if not existing_full_q or not existing_full_q.get(source_language()):
                config_q['full_question'] = dict(lss_q.texts.question)
                updated_count += 1
                logger.debug(f"full_question added for {config_id}")
            else:
                logger.debug(f"full_question present already for {config_id}, skipping")
        
        # add the help text if missing
        if lss_q.texts.help:
            if 'help' not in config_q:
                config_q['help'] = {}
            for lang, text in lss_q.texts.help.items():
                if lang not in config_q['help'] or not config_q['help'][lang]:
                    config_q['help'][lang] = text
    
    # save the configuration, after a snapshot of the previous version
    from services.config_io import write_active_config_with_snapshot
    write_active_config_with_snapshot(config, reason='lss_import')
    
    # reload
    load_config()
    
    return f"\n📋 LSS: complete texts added to {updated_count} questions (survey: {survey_data.survey_id}, languages: {', '.join(survey_data.languages)})"


def _validate_columns(df: pd.DataFrame) -> str:
    """
    Validates the DataFrame columns against the configuration.
    With very high similarity (>98%) the column is mapped automatically.
    
    Returns:
        validation message
    """
    if state.config is None:
        return "⚠️ No configuration loaded"
    
    # collect the configured columns
    configured_columns = set()
    for qconfig in state.config.get('question_groups', {}).values():
        for col in qconfig.get('columns', []):
            configured_columns.add(col)
    
    # columns that segment dimensions refer to directly (source_column)
    for dim in (state.config.get('settings', {}).get('segmentation', {}) or {}).get('dimensions', []) or []:
        if isinstance(dim, dict) and dim.get('source_column'):
            configured_columns.add(dim['source_column'])
    
    # comparison
    df_columns = set(df.columns)
    found = configured_columns & df_columns
    missing = configured_columns - df_columns
    
    lines = []
    auto_mapped = []
    still_missing = []
    
    # automatic mapping of very similar columns
    if missing:
        for config_col in missing:
            similar = find_similar_columns(config_col, df_columns, threshold=0.95)
            
            if similar and similar[0][1] >= 0.98:
                # very high similarity -> map automatically
                csv_col = similar[0][0]
                similarity = similar[0][1]
                
                # rename the DataFrame column
                df.rename(columns={csv_col: config_col}, inplace=True)
                df_columns.discard(csv_col)
                df_columns.add(config_col)
                
                auto_mapped.append((config_col, csv_col, similarity))
                logger.info(f"Auto-Mapping: '{csv_col}' -> '{config_col}' ({similarity:.1%})")
            else:
                still_missing.append((config_col, similar))
    
    # build the report
    if auto_mapped:
        lines.append(f"🔄 {len(auto_mapped)} columns mapped automatically:")
        for config_col, csv_col, sim in auto_mapped[:3]:
            # show the difference
            diff = _find_string_diff(csv_col, config_col)
            lines.append(f"  - {diff}")
        if len(auto_mapped) > 3:
            lines.append(f"  ... and {len(auto_mapped) - 3} more")
        lines.append("")
    
    if still_missing:
        lines.append(f"⚠️ {len(still_missing)} configured columns not found:")
        
        for config_col, similar in still_missing[:5]:
            lines.append(f"  - '{config_col[:80]}...'")
            if similar:
                lines.append(f"    → Did you mean: '{similar[0][0][:60]}...' ({similar[0][1]:.0%})?")
        
        if len(still_missing) > 5:
            lines.append(f"  ... and {len(still_missing) - 5} more")
    elif not auto_mapped:
        lines.append(f"✅ All {len(found)} configured columns found")
    else:
        lines.append(f"✅ All columns found (including {len(auto_mapped)} automatic mappings)")
    
    return "\n".join(lines)


def _find_string_diff(s1: str, s2: str) -> str:
    """Finds and describes the difference between two strings."""
    if s1 == s2:
        return f"'{s1[:50]}...' (identical)"
    
    # find the difference
    for i, (c1, c2) in enumerate(zip(s1, s2)):
        if c1 != c2:
            context_start = max(0, i - 10)
            context_end = min(len(s1), i + 20)
            return f"Difference at position {i}: '...{s1[context_start:context_end]}...' vs '...{s2[context_start:context_end]}...'"
    
    # length difference
    if len(s1) != len(s2):
        return f"Length difference: {len(s1)} vs {len(s2)} characters"
    
    return f"'{s1[:50]}...' -> '{s2[:50]}...'"


def find_similar_columns(target: str, candidates: Set[str], threshold: float = 0.6) -> List[Tuple[str, float]]:
    """
    Finds similar column names.
    
    Args:
        target: column name looked for
        candidates: possible column names
        threshold: minimum similarity (0-1)
    
    Returns:
        list of (column name, similarity), sorted by similarity
    """
    similarities = []
    
    for candidate in candidates:
        # sequence similarity
        ratio = SequenceMatcher(None, target.lower(), candidate.lower()).ratio()
        
        if ratio >= threshold:
            similarities.append((candidate, ratio))
    
    return sorted(similarities, key=lambda x: x[1], reverse=True)


def show_string_diff(s1: str, s2: str) -> str:
    """
    Shows the differences between two strings.
    
    Args:
        s1: first string
        s2: second string
    
    Returns:
        formatted diff output
    """
    if s1 == s2:
        return "Identical"
    
    # character-by-character comparison
    diff_positions = []
    for i, (c1, c2) in enumerate(zip(s1, s2)):
        if c1 != c2:
            diff_positions.append(i)
    
    # length difference
    len_diff = len(s1) - len(s2)
    
    result = []
    if diff_positions:
        result.append(f"Differences at position(s): {diff_positions[:5]}")
    if len_diff != 0:
        result.append(f"Length difference: {abs(len_diff)} characters")
    
    # hex comparison for invisible characters
    if s1.strip() == s2.strip() and s1 != s2:
        result.append("Difference only in whitespace/invisible characters")
    
    return "\n".join(result) if result else "Unknown difference"


def sanitize_id(text: str) -> str:
    """
    Creates a safe id from a text.
    
    Args:
        text: input text
    
    Returns:
        cleaned id (lower case, underscores)
    """
    # replace umlauts
    replacements = {
        'ä': 'ae', 'ö': 'oe', 'ü': 'ue', 'ß': 'ss',
        'Ä': 'Ae', 'Ö': 'Oe', 'Ü': 'Ue'
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # only alphanumerics and underscores
    text = re.sub(r'[^a-zA-Z0-9_]', '_', text)
    
    # collapse repeated underscores
    text = re.sub(r'_+', '_', text)
    
    # remove leading/trailing underscores
    text = text.strip('_')
    
    return text.lower()


def _is_qualtrics_csv(filepath: Path) -> bool:
    """A Qualtrics CSV export: the third line holds {"ImportId": ...} cells."""
    if filepath.suffix.lower() != '.csv':
        return False
    try:
        with open(filepath, encoding='utf-8-sig', errors='replace') as f:
            lines = [f.readline() for _ in range(3)]
        return 'ImportId' in lines[2]
    except OSError:
        return False


def _active_config() -> dict:
    config_path = get_active_config_dir() / "questions.yaml"
    if not config_path.exists():
        return {}
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _apply_data_structure(df: pd.DataFrame) -> str:
    """A first configuration inferred from the data (utils/structure_data.py), with notes on what to check."""
    from utils.languages import get_source_language
    from utils.structure_data import infer_structure
    existing = _active_config()
    structure, notes = infer_structure(df, get_source_language(existing))
    status = _apply_structure(structure)
    lines = [status, "ℹ️ No structure file: the configuration was inferred from the data. Check it in the "
                     "Configuration tab (question types, order of scales, labels)."]
    lines += [f"   • {n}" for n in notes]
    return "\n".join(lines)


def _apply_structure(structure) -> str:
    """
    Configuration from a survey structure (utils/survey_structure.py) for the
    active survey, if its configuration is still empty; otherwise a note.
    """
    config_path = get_active_config_dir() / "questions.yaml"
    existing = {}
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            existing = yaml.safe_load(f) or {}
    if existing.get('question_groups'):
        return (f"\nℹ️ A configuration already exists; the structure from the {structure.source_format.upper()} file "
                f"was not applied")
    from utils.survey_structure import build_config
    return _write_config(*build_config(structure), existing, config_path)


def _write_generated_config(survey_data, data_columns: List[str], existing: dict, config_path) -> str:
    """Configuration from the LSS file (utils/lss_config.py); settings already present are kept."""
    from utils.lss_config import build_config
    return _write_config(*build_config(survey_data, data_columns), existing, config_path)


def _write_config(generated: dict, report: dict, existing: dict, config_path) -> str:
    """Write a generated configuration; settings already present are kept."""
    settings = dict(generated.get("settings") or {})
    settings.update(existing.get("settings") or {})     # an existing settings block wins
    config = {**existing, "settings": settings, "question_groups": generated["question_groups"]}
    # write with a snapshot of the previous file (like every write of questions.yaml)
    from services.config_io import write_active_config_with_snapshot
    write_active_config_with_snapshot(config, reason=f"{report.get('source_format') or 'lss'}_import",
                                      questions_path=config_path)
    source = (report.get("source_format") or "lss").upper()
    if source == "DATA":
        source = "data"
    lines = [f"\n✅ Configuration created from the {source} file: {len(config['question_groups'])} questions"
             if source != "data" else f"\n✅ Configuration created from the data: {len(config['question_groups'])} questions"]
    for title, reason in report["skipped"].items():
        lines.append(f"⚠️ {title} not configured: {reason}")
    for title, missing in report["missing"].items():
        lines.append(f"⚠️ {title}: columns not found ({missing})")
    if report["unmatched_columns"]:
        lines.append(f"ℹ️ {len(report['unmatched_columns'])} columns without a question: "
                     f"{', '.join(report['unmatched_columns'][:8])}")
    return "\n".join(lines)


def guess_handler_type(column_name: str, series: pd.Series) -> str:
    """
    Guesses the handler type from the column name and the data.
    
    Args:
        column_name: name of the column
        series: pandas Series with the data
    
    Returns:
        suggested handler type
    """
    col_lower = column_name.lower()
    
    # free-text indicators (de, en, fr, it); only the fallback without an LSS file
    freetext_keywords = ['sonstig', 'andere', 'kommentar', 'bemerkung', 'freitext',
                         'erläuter', 'beschreib', 'nennen sie', 'welche',
                         'other', 'comment', 'remark', 'please specify', 'describe',
                         'autre', 'commentaire', 'précise', 'décri',
                         'altro', 'commento', 'specificare', 'descriv']
    if any(kw in col_lower for kw in freetext_keywords):
        return 'freetext'
    
    # analyse the data
    non_null = series.dropna()
    if len(non_null) == 0:
        return 'single_choice'
    
    unique_count = non_null.nunique()
    avg_length = non_null.astype(str).str.len().mean()
    
    # long texts -> free text
    if avg_length > 100:
        return 'freetext'
    
    # few distinct values -> single choice
    if unique_count <= 10 and unique_count / len(non_null) < 0.3:
        return 'single_choice'
    
    # yes/no patterns
    values_lower = set(str(v).lower().strip() for v in non_null.unique())
    if values_lower.issubset({'ja', 'nein', 'yes', 'no', 'oui', 'non', 'sì', 'si', '1', '0', 'true', 'false'}):
        return 'single_choice'
    
    # indicators of a Likert scale
    likert_keywords = ['zufrieden', 'wichtig', 'stimme', 'agree', 'satisf', 'important',
                       "d'accord", 'soddisf', 'importan', "d'accordo"]
    if any(kw in col_lower for kw in likert_keywords):
        return 'matrix_likert'
    
    # indicators of a ranking
    if 'rang' in col_lower or 'rank' in col_lower or 'priorit' in col_lower:
        return 'ranking'
    
    # Default
    return 'single_choice'


def _source_language() -> str:
    from utils.prompts import source_language
    return source_language()


def generate_config_from_df(df: pd.DataFrame) -> str:
    """
    Creates a YAML configuration from the DataFrame.
    
    Args:
        df: DataFrame with the survey data
    
    Returns:
        YAML string with the suggested configuration
    """
    config = {
        'settings': {
            'segmentation': {
                'dimensions': []
            }
        },
        'question_groups': {}
    }
    
    for col in df.columns:
        # create the id
        qid = sanitize_id(col[:50])
        
        # guess the handler
        handler = guess_handler_type(col, df[col])
        
        # create the configuration
        config['question_groups'][qid] = {
            'id': qid,
            'label': {_source_language(): col[:100]},   # column name, in the source language
            'type': handler,
            'handler': handler,
            'columns': [col],
            'charts': ['bar_horizontal', 'pie'] if handler == 'single_choice' else ['bar_horizontal'],
            'default_chart': 'bar_horizontal'
        }
    
    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)
