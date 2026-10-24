# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Survey Analysis Suite: main application
=======================================
Gradio web interface for analysing survey data.

Modules:
- core.py: AppState, configuration, session
- services/: the business logic behind the tabs (import, analysis,
  Workbench, pipeline, configuration assistant, setup)
- export/: Word, JSON/CSV and dashboard exports, pipeline
- handlers/: analysis per question type
"""

import os
import logging
from pathlib import Path

# load .env BEFORE other modules are imported
from dotenv import load_dotenv
load_dotenv()

import gradio as gr
import pandas as pd

# own modules
from services.ui_values import ALL, NO_SEGMENTATION, choice_all, pairs
from services.ui_tables import ITEMS, SIMILAR_ITEMS, TRANSLATIONS
from services.workbench_store import APPLY_NO, APPLY_YES
from core import (
    state, 
    load_config, 
    save_config, 
    restore_last_session, 
    get_database_status,
    get_config_yaml,
    CONFIG_DIR,
    get_active_config_dir,
    OUTPUT_DIR
)
from services.question_analysis import (
    get_segmentation_options,
    apply_segmentation,
    get_segment_values,
    set_segment_filter,
    get_question_groups,
    analyze_question
)
from batch_export import (
    export_results,
    analyze_all_and_export_segmented_report,
    export_dashboard_data
)
from services.data_import import (
    import_data,
    find_similar_columns,
    sanitize_id,
    guess_handler_type,
    generate_config_from_df
)
from services.item_extraction import (
    get_freetext_questions,
    get_extraction_status_text,
    preview_item_extraction,
    run_item_extraction,
    run_all_extractions
)
from services.workbench_store import (
    get_freetext_questions as wb_get_freetext_questions,
    get_clusters_for_question,
    load_cluster_editor,
    save_cluster_changes,
    load_items_table,
    load_similar_items_table,
    update_cluster,
    create_cluster,
    delete_cluster,
    update_item_text,
    delete_items,
    merge_items,
    split_item,
    reassign_items_to_cluster,
    find_similar_items,
    apply_normalization,
    recluster_with_categories
)

# handlers and theme analyzer
from handlers import get_handler, HANDLER_REGISTRY
from themes.theme_analyzer import ThemeAnalyzer
from utils.llm import get_llm_client

# service layer: the business logic behind the interface
from services import (
    translations as svc_translations,
    pipeline as svc_pipeline,
    analysis as svc_analysis,
    workbench as svc_workbench,
    publish as svc_publish,
    themes as svc_themes,
    # configuration assistant + overview
    config_io as svc_config_io,
    config_assistant as svc_config_assistant,
    survey_status as svc_survey_status,
    # numeric Workbench
    numeric_workbench as svc_numeric_workbench,
    # segmentation editor
    segmentation_editor as svc_segmentation_editor,
)


def _gradio_progress_callback(gr_progress):
    """
    Converts a gradio.Progress object into the callback protocol of the services.
    
    Services expect a callable(fraction: float, desc: str). gradio.Progress is
    itself a callable with the signature (fraction, desc=...), so they are
    largely compatible, but this adapter makes it explicit and also accepts
    None without crashing.
    """
    if gr_progress is None:
        return None
    
    def _cb(fraction, desc=""):
        try:
            gr_progress(fraction, desc=desc)
        except Exception as e:
            logger.debug(f"Progress callback error ignored: {e}")
    
    return _cb

# configure the logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Languages offered in the interface: every language with a text catalog in locales/
from utils.languages import available_catalogs as _available_catalogs
_UI_LANGUAGES = _available_catalogs()


def _survey_language_defaults():
    """
    Preselected languages of the language controls: the source language of
    the active survey for single choices, its output languages for exports.
    Only languages the interface offers; set at start-up, on every page load
    and after switching the survey.
    """
    from utils.languages import get_output_languages
    from utils.prompts import source_language
    out = [lang for lang in get_output_languages(state.config) if lang in _UI_LANGUAGES] or ["en"]
    src = source_language()
    return (src if src in _UI_LANGUAGES else out[0]), out

# show the LLM configuration
logger.info(f"LLM_BASE_URL: {os.environ.get('LLM_BASE_URL', 'not set')}")
logger.info(f"LLM_MODEL: {os.environ.get('LLM_MODEL', 'not set')}")

# server configuration from environment variables
SERVER_NAME = os.environ.get("GRADIO_SERVER_NAME", "127.0.0.1")
SERVER_PORT = int(os.environ.get("GRADIO_SERVER_PORT", "7861"))
ROOT_PATH = os.environ.get("GRADIO_ROOT_PATH", "")


def create_interface():
    """Creates the Gradio interface."""
    
    # load the configuration initially
    load_config()
    _src_lang, _out_langs = _survey_language_defaults()
    
    with gr.Blocks(
        title="Survey Analysis Suite",
        analytics_enabled=False
    ) as app:
        
        gr.Markdown(
            """
            # 📊 Survey Analysis Suite
            """,
            elem_classes="main-header"
        )
        
        with gr.Tabs():
            
            # ===== TAB: OVERVIEW =====
            with gr.Tab("🏠 Overview"):
                gr.Markdown("### Survey overview")
                gr.Markdown(
                    "Shows how far the active survey has got "
                    "and suggests the next sensible step."
                )
                
                overview_status_md = gr.Markdown(
                    svc_survey_status.render_status_markdown(
                        svc_survey_status.get_workflow_status(state)
                    ),
                    label="Status"
                )
                
                with gr.Row():
                    overview_refresh_btn = gr.Button("🔄 Refresh status", scale=1)
                
                def refresh_overview():
                    return svc_survey_status.render_status_markdown(
                        svc_survey_status.get_workflow_status(state)
                    )
                
                overview_refresh_btn.click(
                    fn=refresh_overview,
                    outputs=[overview_status_md]
                )
            
            # ===== TAB: CONFIGURATION ASSISTANT =====
            with gr.Tab("🛠️ Config assistant"):
                gr.Markdown("### Configuration assistant")
                gr.Markdown(
                    "Suggests changes to the `questions.yaml` of the active survey. "
                    "A snapshot is taken before every change (see below)."
                )
                
                with gr.Row():
                    with gr.Column(scale=2):
                        gr.Markdown("#### Create suggestions")
                        
                        with gr.Row():
                            cfg_assist_category = gr.Radio(
                                choices=[
                                    ("LLM domain context", "llm_context"),
                                    ("Segmentation dimensions", "segmentation"),
                                    ("Numeric settings", "numeric"),
                                    ("Schema overrides", "schema"),
                                    ("Everything", "all"),
                                ],
                                value="all",
                                label="Area",
                            )
                            cfg_assist_scope = gr.Radio(
                                choices=[
                                    ("Unconfigured settings only", "unconfigured"),
                                    ("Overwrite existing settings too", "all"),
                                ],
                                value="unconfigured",
                                label="Scope",
                            )
                        
                        cfg_assist_generate_btn = gr.Button(
                            "🔍 Create suggestions",
                            variant="primary"
                        )
                    
                    with gr.Column(scale=1):
                        gr.Markdown("#### Notes")
                        gr.Markdown(
                            "- A **working LLM connection** is required.\n"
                            "- With *unconfigured*, existing settings are not "
                            "overwritten.\n"
                            "- Numeric settings use sample values from the data "
                            "(if imported).\n"
                            "- Schema overrides are rarely needed and are only "
                            "suggested with high confidence (≥ 0.6)."
                        )
                
                cfg_assist_status = gr.Textbox(
                    label="Status",
                    lines=3,
                    interactive=False,
                    value="Ready. Click 'Create suggestions' to analyze."
                )
                
                gr.Markdown("---")
                gr.Markdown("#### Suggestions (preview)")
                
                cfg_assist_preview = gr.Dataframe(
                    headers=["Category", "Target", "Confidence", "Reason", "Suggested value"],
                    datatype=["str", "str", "number", "str", "str"],
                    label="Suggestions",
                    interactive=False,
                    wrap=True,
                )
                
                # hidden state: keeps the last suggestions as JSON, so applying/discarding
                # finds the originals
                cfg_assist_state = gr.State(value=[])
                
                with gr.Row():
                    cfg_assist_apply_all_btn = gr.Button(
                        "✅ Apply all",
                        variant="primary"
                    )
                    cfg_assist_clear_btn = gr.Button("🗑️ Discard suggestions")
                
                gr.Markdown("---")
                gr.Markdown("#### Snapshots")
                
                cfg_assist_snapshots_md = gr.Markdown(
                    "_No snapshots listed yet. Click 'Refresh'._"
                )
                
                with gr.Row():
                    cfg_assist_refresh_snaps_btn = gr.Button(
                        "🔄 Refresh snapshots"
                    )
                    cfg_assist_restore_btn = gr.Button(
                        "↩️ Restore the last snapshot"
                    )
                
                cfg_assist_restore_status = gr.Textbox(
                    label="Snapshot status",
                    lines=2,
                    interactive=False,
                )
                
                # ----- Handler -----
                
                def cfg_assist_generate(category, scope):
                    """Creates suggestions for the chosen area."""
                    if state.config is None:
                        load_config()
                    
                    if state.config is None:
                        return (
                            "❌ No configuration loaded. Import data first.",
                            [],
                            []
                        )
                    
                    # create the suggestions for the chosen area
                    try:
                        if category == "llm_context":
                            s = svc_config_assistant.suggest_llm_context(state)
                            suggestions = [s] if s else []
                        elif category == "segmentation":
                            suggestions = svc_config_assistant.suggest_segmentation_dimensions(state)
                        elif category == "numeric":
                            suggestions = svc_config_assistant.suggest_numeric_fields(state, scope=scope)
                        elif category == "schema":
                            suggestions = svc_config_assistant.suggest_schema_overrides(state, scope=scope)
                        else:  # 'all'
                            suggestions = svc_config_assistant.generate_all_suggestions(state, scope=scope)
                    except Exception as e:
                        logger.exception("Error creating the suggestions")
                        return f"❌ Error: {e}", [], []
                    
                    if not suggestions:
                        return (
                            "ℹ️ No suggestions created: either the LLM is not reachable "
                            "or no relevant questions were found.",
                            [],
                            []
                        )
                    
                    # build the table data
                    rows = []
                    for s in suggestions:
                        # show the value compactly
                        val_str = str(s.new_value)
                        if len(val_str) > 200:
                            val_str = val_str[:200] + "..."
                        rows.append([
                            s.category,
                            s.target,
                            round(s.confidence, 2),
                            s.reason[:200],
                            val_str,
                        ])
                    
                    # suggestions as a serialisable dict in the state
                    serialized = [
                        {
                            'category': s.category,
                            'target': s.target,
                            'action': s.action,
                            'new_value': s.new_value,
                            'reason': s.reason,
                            'confidence': s.confidence,
                        }
                        for s in suggestions
                    ]
                    
                    status_text = (
                        f"✅ {len(suggestions)} suggestions created.\n"
                        f"Click 'Apply all' to write them to questions.yaml "
                        f"(a snapshot is taken first)."
                    )
                    return status_text, rows, serialized
                
                cfg_assist_generate_btn.click(
                    fn=lambda: ("⏳ Creating suggestions...", [], []),
                    outputs=[cfg_assist_status, cfg_assist_preview, cfg_assist_state]
                ).then(
                    fn=cfg_assist_generate,
                    inputs=[cfg_assist_category, cfg_assist_scope],
                    outputs=[cfg_assist_status, cfg_assist_preview, cfg_assist_state]
                )
                
                def cfg_assist_apply_all(serialized_suggestions):
                    """Applies all suggestions to the configuration, writes it and takes a snapshot."""
                    if not serialized_suggestions:
                        return "⚠️ No suggestions to apply.", [], []
                    
                    # rebuild the Suggestion objects
                    suggestions = [
                        svc_config_assistant.Suggestion(**s)
                        for s in serialized_suggestions
                    ]
                    
                    try:
                        # load the current configuration
                        current = svc_config_io.load_active_config()
                        # apply (to a copy)
                        new_config = svc_config_assistant.apply_suggestions(
                            current, suggestions
                        )
                        # write with a snapshot
                        svc_config_io.write_active_config_with_snapshot(
                            new_config, reason='assistant'
                        )
                        # update the state in memory as well
                        load_config()
                    except Exception as e:
                        logger.exception("Error applying the suggestions")
                        return f"❌ Error: {e}", [], serialized_suggestions
                    
                    return (
                        f"✅ {len(suggestions)} suggestions applied. "
                        f"A snapshot of the previous version was taken.",
                        [],
                        []
                    )
                
                cfg_assist_apply_all_btn.click(
                    fn=cfg_assist_apply_all,
                    inputs=[cfg_assist_state],
                    outputs=[cfg_assist_status, cfg_assist_preview, cfg_assist_state]
                )
                
                cfg_assist_clear_btn.click(
                    fn=lambda: ("Suggestions discarded.", [], []),
                    outputs=[cfg_assist_status, cfg_assist_preview, cfg_assist_state]
                )
                
                # ----- Snapshots -----
                
                def cfg_assist_list_snapshots():
                    try:
                        snaps = svc_config_io.list_snapshots()
                    except Exception as e:
                        return f"❌ Error listing the snapshots: {e}"
                    
                    if not snaps:
                        return "_No snapshots yet._"
                    
                    lines = ["**Available snapshots** (newest first):", ""]
                    for path, ts in snaps[:10]:
                        lines.append(
                            f"- `{path.name}` "
                            f"({ts.strftime('%Y-%m-%d %H:%M:%S')})"
                        )
                    return '\n'.join(lines)
                
                def cfg_assist_restore_latest():
                    try:
                        snaps = svc_config_io.list_snapshots()
                    except Exception as e:
                        return f"❌ Error: {e}"
                    
                    if not snaps:
                        return "⚠️ No snapshots available."
                    
                    latest_path = snaps[0][0]
                    try:
                        svc_config_io.restore_snapshot(latest_path)
                        load_config()
                    except Exception as e:
                        return f"❌ Error restoring: {e}"
                    
                    return (
                        f"✅ Snapshot restored: `{latest_path.name}`. "
                        f"Configuration reloaded."
                    )
                
                cfg_assist_refresh_snaps_btn.click(
                    fn=cfg_assist_list_snapshots,
                    outputs=[cfg_assist_snapshots_md]
                )
                
                cfg_assist_restore_btn.click(
                    fn=cfg_assist_restore_latest,
                    outputs=[cfg_assist_restore_status]
                ).then(
                    fn=cfg_assist_list_snapshots,
                    outputs=[cfg_assist_snapshots_md]
                )
            
            # ===== TAB: IMPORT =====
            with gr.Tab("📥 Import"):
                gr.Markdown("### Import data")
                
                with gr.Row():
                    with gr.Column(scale=2):
                        file_input = gr.File(
                            label="Upload a CSV/Excel/SPSS file (survey responses)",
                            file_types=[".csv", ".xlsx", ".xls", ".sav"]
                        )
                        lss_input = gr.File(
                            label="📋 Structure (LimeSurvey .lss, Qualtrics .qsf), optional",
                            file_types=[".lss", ".qsf"],
                            file_count="single"
                        )
                        gr.Markdown(
                            "*The structure file adds the question texts in all languages; without a configuration, it creates one.*",
                            elem_classes=["hint-text"]
                        )
                        import_btn = gr.Button("📥 Import", variant="primary")
                        restore_btn = gr.Button("🔄 Restore the last session")
                    
                    with gr.Column(scale=3):
                        import_status = gr.Textbox(
                            label="Import status",
                            lines=8,
                            interactive=False
                        )
                
                with gr.Accordion("📊 Data preview", open=False):
                    data_preview = gr.Dataframe(
                        label="Preview (first 20 rows)",
                        interactive=False,
                        wrap=True
                    )
                
                with gr.Accordion("💾 Database status", open=False):
                    db_status = gr.Markdown("")
                    refresh_db_btn = gr.Button("🔄 Refresh status")
                
                with gr.Accordion("🌐 Translate answers", open=False):
                    gr.Markdown(
                        "Translates all free-text answers into the target language for a consistent analysis. "
                        "Answers in other languages are detected and translated."
                    )
                    with gr.Row():
                        translate_target_lang = gr.Dropdown(
                            choices=_UI_LANGUAGES,
                            value=_src_lang,
                            label="Target language"
                        )
                        translate_btn = gr.Button("🌐 Translate answers", variant="primary")
                    translate_status = gr.Textbox(
                        label="Translation status",
                        lines=5,
                        interactive=False
                    )
                    
                    with gr.Accordion("📋 Review and edit translations", open=False):
                        gr.Markdown(
                            "💡 **Tip:** Click into the translation column to correct a text, "
                            "then click 'Save changes'."
                        )
                        translation_preview = gr.Dataframe(
                            label="Translation table (editable)",
                            headers=TRANSLATIONS.labels,
                            interactive=True,
                            wrap=True,
                            max_height=400,
                            column_widths=["50px", "35%", "80px", "45%"]
                        )
                        with gr.Row():
                            refresh_translations_btn = gr.Button("🔄 Load translations")
                            save_translations_btn = gr.Button("💾 Save changes", variant="primary")
                            delete_selected_btn = gr.Button("🗑️ Delete all translations", variant="stop")
                        delete_translations_confirm = gr.Checkbox(
                            label="Confirm deletion: affects all surveys, including manual corrections",
                            value=False,
                        )
                        translation_save_status = gr.Textbox(
                            label="Status",
                            lines=2,
                            interactive=False
                        )
                
                # note after the import: the pipeline is a tab of its own
                gr.Markdown(
                    "---\n"
                    "💡 **After the import:** go to the **▶️ Pipeline** tab "
                    "to run the complete analysis."
                )
                
                # Event Handler
                import_btn.click(
                    fn=import_data,
                    inputs=[file_input, lss_input],
                    outputs=[import_status, data_preview]
                ).then(
                    fn=get_database_status,
                    outputs=[db_status]
                )
                
                restore_btn.click(
                    fn=restore_last_session,
                    outputs=[import_status, data_preview]
                ).then(
                    fn=get_database_status,
                    outputs=[db_status]
                )
                
                refresh_db_btn.click(
                    fn=get_database_status,
                    outputs=[db_status]
                )
                
                # load the translations from the database
                def load_translations_from_db(target_lang="de"):
                    """Loads all translations from the database as a table."""
                    return svc_translations.load_translations_from_db(state, target_lang)
                
                # save the changes
                def save_translations_to_db(df, target_lang="de"):
                    """Saves changed translations in the database."""
                    return svc_translations.save_translations_to_db(state, df, target_lang)
                
                # delete all translations
                def delete_all_translations(target_lang="de", confirmed=False):
                    """Deletes all translations into the target language."""
                    return svc_translations.delete_all_translations(
                        state, target_lang, confirmed=confirmed
                    )
                
                # translation handler
                def translate_responses_handler(target_lang, progress=gr.Progress()):
                    # make sure the configuration is loaded (the service does not load implicitly)
                    if state.config is None:
                        load_config()
                    return svc_translations.translate_responses(
                        state, target_lang,
                        progress=_gradio_progress_callback(progress),
                    )
                
                translate_btn.click(
                    fn=lambda: ("⏳ Starting translation...", None),
                    outputs=[translate_status, translation_preview]
                ).then(
                    fn=translate_responses_handler,
                    inputs=[translate_target_lang],
                    outputs=[translate_status, translation_preview]
                )
                
                refresh_translations_btn.click(
                    fn=load_translations_from_db,
                    inputs=[translate_target_lang],
                    outputs=[translation_preview]
                )
                
                save_translations_btn.click(
                    fn=save_translations_to_db,
                    inputs=[translation_preview, translate_target_lang],
                    outputs=[translation_save_status]
                ).then(
                    fn=lambda: state.mark_reexport_needed("Translations changed"),
                    outputs=[],
                )
                
                delete_selected_btn.click(
                    fn=delete_all_translations,
                    inputs=[translate_target_lang, delete_translations_confirm],
                    outputs=[translation_save_status, translation_preview]
                )
            
            # ===== TAB: PIPELINE =====
            # complete pipeline, regenerating the summaries and the re-export after
            # Workbench changes in a tab of their own
            with gr.Tab("▶️ Pipeline"):
                gr.Markdown("### Complete analysis pipeline")
                gr.Markdown(
                    "Prerequisite: data imported in the **📥 Import** tab. "
                    "Optional: configuration checked in the **🛠️ Config assistant**."
                )
                
                with gr.Accordion("🚀 Complete pipeline", open=True):
                    gr.Markdown("""
                    **Runs all analysis steps automatically:**
                    1. Item extraction for free-text questions
                    2. Analysis of all questions (including clustering)
                    3. Theme analysis and assignment
                    4. Word reports in all output languages
                    5. Dashboard export (JSON)
                    
                    ⏱️ *Duration: 5–30 minutes, depending on the amount of data and the speed of the LLM*
                    """)
                    
                    with gr.Row():
                        with gr.Column():
                            pipeline_item_extraction = gr.Checkbox(
                                label="Item extraction",
                                value=True,
                                info="Split free text into single items"
                            )
                            pipeline_theme_analysis = gr.Checkbox(
                                label="Theme analysis",
                                value=True,
                                info="Extract keywords and assign questions"
                            )
                            pipeline_deep_analysis = gr.Checkbox(
                                label="Deep theme analysis",
                                value=False,
                                info="Check every cluster with the LLM (slower, more accurate)"
                            )
                            pipeline_segment_comparison = gr.Checkbox(
                                label="Segment comparison",
                                value=True,
                                info="Segment-specific themes and tested differences between groups"
                            )
                            pipeline_summaries = gr.Checkbox(
                                label="LLM summaries",
                                value=True,
                                info="Description, interpretation and segment analysis for each question"
                            )
                        with gr.Column():
                            pipeline_languages = gr.CheckboxGroup(
                                choices=_UI_LANGUAGES,
                                value=_out_langs,
                                label="Export languages"
                            )
                    
                    pipeline_btn = gr.Button(
                        "🚀 Start the complete pipeline", 
                        variant="primary",
                        size="lg"
                    )
                
                with gr.Accordion("📝 Regenerate summaries", open=False):
                    gr.Markdown("""
                    Clears the summary cache and generates all texts again.
                    Useful after changing prompts or the model.
                    Analysis results are kept.
                    """)
                    
                    regen_include_word = gr.Checkbox(
                        label="Update the Word reports",
                        value=True,
                        info="Recreate the Word reports with the new summaries"
                    )
                    
                    regen_summaries_btn = gr.Button(
                        "📝 Regenerate summaries",
                        variant="secondary"
                    )
                
                with gr.Accordion("🔄 Re-export after Workbench changes", open=False):
                    gr.Markdown("""
                    Recreates the Word reports and the dashboard export from the current database.
                    With changed clusters, items are reassigned and translations updated.
                    """)
                    
                    reexport_reassign = gr.Checkbox(
                        label="Reassign items (LLM)",
                        value=True,
                        info="With changed clusters: the LLM assigns all items to the new cluster definitions"
                    )
                    
                    reexport_btn = gr.Button(
                        "🔄 Start the re-export", 
                        variant="secondary"
                    )
                
                gr.Markdown("---")
                
                pipeline_status = gr.Textbox(
                    label="Pipeline status",
                    lines=15,
                    interactive=False
                )
                
                pipeline_output_path = gr.Textbox(
                    label="Export directory",
                    interactive=False
                )
                
                # ----- Pipeline-Handler -----
                
                def run_pipeline_handler(
                    include_item_extraction,
                    include_theme_analysis,
                    deep_theme_analysis,
                    include_segment_comparison,
                    include_summaries,
                    languages,
                    progress=gr.Progress()
                ):
                    return svc_pipeline.run_full_pipeline(
                        state,
                        include_item_extraction=include_item_extraction,
                        include_theme_analysis=include_theme_analysis,
                        deep_theme_analysis=deep_theme_analysis,
                        include_segment_comparison=include_segment_comparison,
                        include_summaries=include_summaries,
                        languages=languages,
                        progress=_gradio_progress_callback(progress),
                    )
                
                pipeline_btn.click(
                    fn=lambda: ("🚀 Starting the pipeline...\n\nThis can take several minutes.", ""),
                    outputs=[pipeline_status, pipeline_output_path]
                ).then(
                    fn=run_pipeline_handler,
                    inputs=[
                        pipeline_item_extraction,
                        pipeline_theme_analysis,
                        pipeline_deep_analysis,
                        pipeline_segment_comparison,
                        pipeline_summaries,
                        pipeline_languages
                    ],
                    outputs=[pipeline_status, pipeline_output_path]
                )
                
                def run_regen_summaries_handler(include_word, languages, progress=gr.Progress()):
                    return svc_pipeline.regenerate_summaries(
                        state,
                        include_word=include_word,
                        languages=languages,
                        progress=_gradio_progress_callback(progress),
                    )
                
                regen_summaries_btn.click(
                    fn=lambda: ("📝 Regenerating summaries...\n\nClearing the cache...", ""),
                    outputs=[pipeline_status, pipeline_output_path]
                ).then(
                    fn=run_regen_summaries_handler,
                    inputs=[regen_include_word, pipeline_languages],
                    outputs=[pipeline_status, pipeline_output_path]
                )
                
                def run_reexport_handler(languages, reassign_items, progress=gr.Progress()):
                    status, output = svc_pipeline.reexport_after_workbench(
                        state,
                        languages=languages,
                        reassign_items=reassign_items,
                        progress=_gradio_progress_callback(progress),
                    )
                    # clear the flag after a successful re-export
                    if output:   # success: the export directory (failures return "")
                        state.clear_reexport_flag()
                    return status, output
                
                reexport_btn.click(
                    fn=lambda: ("🔄 Starting the re-export...\n\nLoading data from the database...", ""),
                    outputs=[pipeline_status, pipeline_output_path]
                ).then(
                    fn=run_reexport_handler,
                    inputs=[pipeline_languages, reexport_reassign],
                    outputs=[pipeline_status, pipeline_output_path]
                )
            
            # ===== TAB: CONFIGURATION =====
            with gr.Tab("⚙️ Configuration"):
                gr.Markdown("### Configure question groups")
                
                with gr.Row():
                    with gr.Column(scale=3):
                        config_editor = gr.Code(
                            label="questions.yaml",
                            language="yaml",
                            lines=30,
                            value=get_config_yaml()
                        )
                    
                    with gr.Column(scale=1):
                        save_config_btn = gr.Button("💾 Save", variant="primary")
                        reload_config_btn = gr.Button("🔄 Reload")
                        config_status = gr.Textbox(
                            label="Status",
                            lines=3,
                            interactive=False
                        )
                        
                        gr.Markdown("### Available handlers")
                        handler_info = gr.Markdown(
                            "\n".join(f"- `{h}`" for h in HANDLER_REGISTRY.keys())
                        )
            
            # ===== TAB: ITEM EXTRACTION =====
            with gr.Tab("🔧 Item extraction"):
                gr.Markdown("### Extract items from free-text answers")
                gr.Markdown(
                    "Extracts single mentions from free-text answers for "
                    "the cluster analysis. An answer like 'Cloud, AI and security' "
                    "becomes three separate items."
                )
                
                # === BATCH EXTRACTION ===
                with gr.Accordion("🚀 Extract all free-text questions", open=True):
                    gr.Markdown(
                        "Extracts items for **all** free-text questions at once. "
                        "This is required before the complete analysis."
                    )
                    with gr.Row():
                        batch_force_llm = gr.Checkbox(
                            label="Force the LLM for all",
                            value=False
                        )
                        batch_force_reextract = gr.Checkbox(
                            label="Extract all again",
                            value=False,
                            info="If off: questions already extracted are skipped"
                        )
                    with gr.Row():
                        batch_thematic_mode = gr.Checkbox(
                            label="Thematic splitting",
                            value=True,
                            info="Splits by topic (e.g. 'AI for security and cloud migration' → 2 items). Details are kept."
                        )
                        batch_validate = gr.Checkbox(
                            label="Quality check",
                            value=True,
                            info="The LLM checks that details were kept. If the extraction is too abstract, it is retried."
                        )
                    with gr.Row():
                        batch_extract_btn = gr.Button(
                            "🚀 Extract all free-text questions",
                            variant="primary"
                        )
                    batch_result = gr.Markdown("")
                    batch_stats_json = gr.JSON(label="Batch statistics", visible=False)
                
                gr.Markdown("---")
                
                # === SINGLE EXTRACTION ===
                gr.Markdown("### Extract a single question")
                with gr.Row():
                    with gr.Column(scale=1):
                        extraction_question = gr.Dropdown(
                            choices=get_freetext_questions(),
                            label="Free-text question"
                        )
                        force_llm_checkbox = gr.Checkbox(
                            label="Force LLM extraction",
                            value=False
                        )
                        thematic_checkbox = gr.Checkbox(
                            label="Thematic splitting",
                            value=True,
                            info="Splits by topic, keeps details"
                        )
                        
                        with gr.Row():
                            preview_btn = gr.Button("👁️ Preview")
                            extract_btn = gr.Button("▶️ Extract", variant="primary")
                        
                        extraction_result = gr.Textbox(
                            label="Result",
                            lines=6,
                            interactive=False
                        )
                    
                    with gr.Column(scale=2):
                        extraction_preview = gr.Markdown("")
                        extraction_stats_json = gr.JSON(label="Statistics")
                
                with gr.Accordion("📊 Extraction status", open=False):
                    extraction_status = gr.Markdown("")
                    refresh_extraction_btn = gr.Button("🔄 Refresh")
                
                # extract all free-text questions (with progress)
                def batch_extract_all(force_llm, force_reextract, thematic_mode, validate, progress=gr.Progress()):
                    """Extracts items for all free-text questions."""
                    # make sure the configuration is loaded (the service does not load implicitly)
                    if state.config is None:
                        load_config()
                    return svc_pipeline.batch_extract_items(
                        state,
                        force_llm=force_llm,
                        force_reextract=force_reextract,
                        thematic_mode=thematic_mode,
                        validate=validate,
                        progress=_gradio_progress_callback(progress),
                    )
                
                # event handler, batch (progress works only in .then())
                batch_extract_btn.click(
                    fn=lambda: ("⏳ Starting extraction...", "{}"),
                    outputs=[batch_result, batch_stats_json]
                ).then(
                    fn=batch_extract_all,
                    inputs=[batch_force_llm, batch_force_reextract, batch_thematic_mode, batch_validate],
                    outputs=[batch_result, batch_stats_json]
                ).then(
                    fn=get_extraction_status_text,
                    outputs=[extraction_status]
                )
                
                # event handler, single
                preview_btn.click(
                    fn=preview_item_extraction,
                    inputs=[extraction_question, force_llm_checkbox],
                    outputs=[extraction_preview, extraction_result]
                )
                
                extract_btn.click(
                    fn=run_item_extraction,
                    inputs=[extraction_question, force_llm_checkbox],
                    outputs=[extraction_result, extraction_stats_json]
                ).then(
                    fn=get_extraction_status_text,
                    outputs=[extraction_status]
                )
                
                refresh_extraction_btn.click(
                    fn=get_extraction_status_text,
                    outputs=[extraction_status]
                )
            
            # ===== TAB: ANALYSIS =====
            with gr.Tab("🔍 Analysis"):
                gr.Markdown("### Analyze a question group")
                gr.Markdown(
                    "_Read-only view. To edit clusters, items "
                    "or numeric values, go to the **✏️ Workbench** tab._"
                )
                
                with gr.Row():
                    with gr.Column(scale=1):
                        # segmentation
                        segment_dropdown = gr.Dropdown(
                            choices=get_segmentation_options(),
                            label="Segmentation",
                            value=NO_SEGMENTATION
                        )
                        segment_value = gr.Dropdown(
                            choices=[],
                            label="Segment value",
                            visible=False
                        )
                        segment_status = gr.Textbox(
                            label="Segment status",
                            lines=2,
                            interactive=False
                        )
                        
                        gr.Markdown("---")
                        
                        # Fragenauswahl
                        question_dropdown = gr.Dropdown(
                            choices=get_question_groups(),
                            label="Question group"
                        )
                        lang_dropdown = gr.Dropdown(
                            choices=_UI_LANGUAGES,
                            value=_src_lang,
                            label="Language"
                        )
                        
                        with gr.Row():
                            analyze_btn = gr.Button("🔍 Show", variant="primary")
                            analyze_all_btn = gr.Button("🔄 Analyze all", variant="secondary")
                        
                        force_recalc_analysis = gr.Checkbox(
                            label="Recalculate all",
                            value=False,
                            info="If off: questions already analyzed are skipped"
                        )
                    
                    with gr.Column(scale=2):
                        analyze_all_status = gr.Textbox(
                            label="Batch status",
                            lines=3,
                            interactive=False,
                            visible=True,
                            value=""
                        )
                        result_text = gr.Markdown("")
                        
                        # numeric statistics table (visible only for numeric questions)
                        numeric_stats_table = gr.Dataframe(
                            headers=["Statistics", "Value"],
                            label="Numeric statistics",
                            interactive=False,
                            visible=False,
                            wrap=True,
                        )
                        
                        result_chart = gr.Plot(label="Visualization")
                
                with gr.Accordion("📝 Cluster details (free text)", open=False):
                    cluster_details = gr.Markdown("")
                
                # analyse all questions
                def analyze_all_questions(lang, force_recalculate, progress=gr.Progress()):
                    """Analyses all questions and keeps the results in the state."""
                    # make sure the configuration is loaded
                    if state.config is None:
                        load_config()
                    
                    status, choices = svc_analysis.analyze_all_questions(
                        state, lang=lang, force_recalculate=force_recalculate,
                        progress=_gradio_progress_callback(progress),
                    )
                    return status, gr.Dropdown(choices=choices)
                
                # Event Handler
                def on_segment_change(choice):
                    if choice == NO_SEGMENTATION or not choice:
                        return gr.Dropdown(visible=False), ""
                    status, _ = apply_segmentation(choice)
                    values = get_segment_values()
                    return gr.Dropdown(choices=values, visible=True), status
                
                segment_dropdown.change(
                    fn=on_segment_change,
                    inputs=[segment_dropdown],
                    outputs=[segment_value, segment_status]
                )
                
                segment_value.change(
                    fn=set_segment_filter,
                    inputs=[segment_value],
                    outputs=[segment_status]
                )
                
                # wrapper that, besides analyze_question, extracts the numeric statistics
                # and shows/hides the table
                def analyze_question_with_numeric_stats(qid_choice, lang):
                    text, chart, clusters = analyze_question(qid_choice, lang)
                    
                    # try to read the numeric statistics dict of this question from
                    # state.analysis_results
                    qid = (qid_choice or "").split(":")[0].strip() if qid_choice else None
                    rows = []
                    visible = False
                    if qid and qid in state.analysis_results:
                        result = state.analysis_results[qid]
                        # result may be a dict (cached) or a dataclass
                        metadata = (result.get('metadata') if isinstance(result, dict)
                                    else getattr(result, 'metadata', {})) or {}
                        numeric_stats = metadata.get('numeric_stats')
                        if numeric_stats:
                            visible = True
                            unit = metadata.get('unit', '')
                            unit_suffix = f' {unit}' if unit else ''
                            
                            def _fmt(v, decimals=2):
                                if v is None:
                                    return '—'
                                try:
                                    return f"{float(v):.{decimals}f}"
                                except (TypeError, ValueError):
                                    return str(v)
                            
                            rows = [
                                ["n (valid)", str(numeric_stats.get('n', '—'))],
                                ["n (invalid)", str(numeric_stats.get('n_invalid', 0))],
                                ["Mean", _fmt(numeric_stats.get('mean')) + unit_suffix],
                                ["Median", _fmt(numeric_stats.get('median')) + unit_suffix],
                                ["Standard deviation", _fmt(numeric_stats.get('std'))],
                                ["Minimum", _fmt(numeric_stats.get('min'), 0) + unit_suffix],
                                ["Maximum", _fmt(numeric_stats.get('max'), 0) + unit_suffix],
                                ["Q25", _fmt(numeric_stats.get('q25')) + unit_suffix],
                                ["Q75", _fmt(numeric_stats.get('q75')) + unit_suffix],
                            ]
                            outliers = numeric_stats.get('outliers') or []
                            if outliers:
                                rows.append([
                                    "Outliers (IQR method)",
                                    f"{len(outliers)} value(s)",
                                ])
                    
                    return (
                        text, chart, clusters,
                        gr.update(value=rows, visible=visible),
                    )
                
                analyze_btn.click(
                    fn=analyze_question_with_numeric_stats,
                    inputs=[question_dropdown, lang_dropdown],
                    outputs=[result_text, result_chart, cluster_details, numeric_stats_table]
                )
                
                analyze_all_btn.click(
                    fn=lambda: ("⏳ Starting analysis...", gr.update()),
                    outputs=[analyze_all_status, question_dropdown]
                ).then(
                    fn=analyze_all_questions,
                    inputs=[lang_dropdown, force_recalc_analysis],
                    outputs=[analyze_all_status, question_dropdown]
                )
            
            # ===== TAB: WORKBENCH =====
            with gr.Tab("✏️ Workbench"):
                gr.Markdown("### Editing data and quality assurance")
                gr.Markdown("Here you can rename clusters, edit items and normalize spellings.")
                
                # ----- re-export banner -----
                # shown when state.pending_reexport is set;
                # updated by a click on 'Check re-export status' (no automatic refresh).
                workbench_banner_md = gr.Markdown("", visible=False)
                with gr.Row(visible=True) as workbench_banner_row:
                    workbench_banner_check_btn = gr.Button(
                        "🔄 Check re-export status",
                        scale=1,
                        size="sm",
                    )
                    workbench_banner_reexport_btn = gr.Button(
                        "📤 Re-export now",
                        scale=2,
                        size="sm",
                        variant="primary",
                        visible=False,
                    )
                    workbench_banner_dismiss_btn = gr.Button(
                        "Later",
                        scale=1,
                        size="sm",
                        visible=False,
                    )
                
                with gr.Row():
                    wb_refresh_btn = gr.Button("🔄 Refresh question list", variant="secondary")
                    wb_prepare_all_btn = gr.Button("⚡ Prepare everything (analysis + extraction)", variant="primary")
                
                wb_info = gr.Textbox(label="Status", interactive=False)
                
                with gr.Tabs():
                    # sub-tab: editing clusters
                    with gr.Tab("🏷️ Clusters"):
                        gr.Markdown("#### Edit cluster names and descriptions")
                        
                        wb_cluster_question = gr.Dropdown(
                            label="Free-text question",
                            choices=[],
                            interactive=True
                        )
                        
                        # Columns follow the survey's languages (a name per output
                        # language, the source language first); set on loading a question
                        wb_cluster_table = gr.Dataframe(
                            interactive=True,
                            label="Clusters (double-click to edit)"
                        )
                        
                        with gr.Row():
                            wb_save_clusters_btn = gr.Button("💾 Save changes", variant="primary")
                            wb_add_cluster_btn = gr.Button("➕ Add a cluster")
                            wb_recluster_btn = gr.Button("🔄 Recluster with these names")
                        
                        wb_cluster_status = gr.Textbox(label="Status", interactive=False)
                        
                        with gr.Accordion("🗑️ Delete a cluster", open=False):
                            gr.Markdown("Deletes a cluster; its items go to another cluster or stay without a cluster. "
                                        "To move single items, change their cluster in the **Items** tab.")
                            with gr.Row():
                                wb_delete_cluster = gr.Dropdown(label="Cluster to delete", choices=[], interactive=True)
                                wb_delete_target = gr.Dropdown(label="Move its items to", choices=[], interactive=True)
                            wb_delete_cluster_btn = gr.Button("🗑️ Delete the cluster", variant="stop")
                        
                        def _cluster_name_choices(question_id):
                            from services.workbench_store import NO_CLUSTER
                            names = [str(c['name_source']) for c in get_clusters_for_question(question_id)] if question_id else []
                            return (gr.Dropdown(choices=names, value=None),
                                    gr.Dropdown(choices=[NO_CLUSTER] + names, value=NO_CLUSTER))
                        
                        def delete_cluster_ui(question_id, name, move_to):
                            if not question_id or not name:
                                return "❌ Choose a question and the cluster to delete"
                            from services.workbench_store import delete_cluster_by_name
                            return delete_cluster_by_name(question_id, name, move_to)
                        
                        # event handlers of the clusters tab
                        wb_cluster_question.change(
                            fn=load_cluster_editor,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_table]
                        ).then(
                            fn=_cluster_name_choices,
                            inputs=[wb_cluster_question],
                            outputs=[wb_delete_cluster, wb_delete_target]
                        )
                        
                        wb_delete_cluster_btn.click(
                            fn=delete_cluster_ui,
                            inputs=[wb_cluster_question, wb_delete_cluster, wb_delete_target],
                            outputs=[wb_cluster_status]
                        ).then(
                            fn=load_cluster_editor,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_table]
                        ).then(
                            fn=_cluster_name_choices,
                            inputs=[wb_cluster_question],
                            outputs=[wb_delete_cluster, wb_delete_target]
                        )
                        
                        wb_save_clusters_btn.click(
                            fn=save_cluster_changes,
                            inputs=[wb_cluster_table, wb_cluster_question],
                            outputs=[wb_cluster_status]
                        ).then(
                            fn=load_cluster_editor,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_table]
                        )
                        
                        def add_new_cluster(question_id):
                            if not question_id:
                                return "❌ Select a question first"
                            try:
                                new_id = create_cluster(question_id, "New cluster")
                                return f"✅ New cluster created (ID: {new_id})"
                            except Exception as e:
                                return f"❌ Error: {e}"
                        
                        wb_add_cluster_btn.click(
                            fn=add_new_cluster,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_status]
                        ).then(
                            fn=load_cluster_editor,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_table]
                        )
                        
                        def do_recluster(df, question_id):
                            if df is None or df.empty or not question_id:
                                return "❌ No data available"
                            try:
                                # extract the cluster names
                                from services.workbench_store import source_name_column
                                category_names = df[source_name_column()].tolist()
                                category_names = [n for n in category_names if n and n.strip()]
                                
                                if not category_names:
                                    return "❌ No cluster names available"
                                
                                result = recluster_with_categories(
                                    question_id,
                                    category_names,
                                    allow_new_categories=True,
                                    dry_run=False
                                )
                                
                                stats_str = ', '.join(f"{k}: {v}" for k, v in result['stats'].items())
                                new_cats = f", new: {result['new_categories']}" if result['new_categories'] else ""
                                return f"✅ Reclustering finished. {stats_str}{new_cats}"
                            except Exception as e:
                                logger.exception("Reclustering error")
                                return f"❌ Error: {e}"
                        
                        wb_recluster_btn.click(
                            fn=do_recluster,
                            inputs=[wb_cluster_table, wb_cluster_question],
                            outputs=[wb_cluster_status]
                        ).then(
                            fn=load_cluster_editor,
                            inputs=[wb_cluster_question],
                            outputs=[wb_cluster_table]
                        )
                    
                    # sub-tab: editing items
                    with gr.Tab("📝 Items"):
                        gr.Markdown("#### Edit extracted items")
                        gr.Markdown("Edit the text in *Extracted*; to move an item to another cluster, type the "
                                    "cluster's name in *Cluster* (empty: no cluster). *Save changes* applies both.")
                        
                        with gr.Row():
                            wb_item_question = gr.Dropdown(
                                label="Question",
                                choices=[],
                                interactive=True
                            )
                            wb_item_cluster = gr.Dropdown(
                                label="Cluster filter",
                                choices=[choice_all()],
                                value=ALL,
                                interactive=True
                            )
                            wb_item_search = gr.Textbox(
                                label="Search",
                                placeholder="Enter a search term..."
                            )
                        
                        wb_items_table = gr.Dataframe(
                            headers=ITEMS.labels,
                            datatype=['number', 'str', 'str', 'str'],
                            # Gradio 6: column_count=(N, 'fixed') -> column_count + column_limits
                            column_count=4,
                            column_limits=(4, 4),
                            interactive=True,
                            label="Items (double-click to edit)"
                        )
                        
                        wb_item_info = gr.Markdown("")
                        
                        with gr.Row():
                            wb_refresh_items_btn = gr.Button("🔄 Refresh")
                            wb_save_items_btn = gr.Button("💾 Save changes", variant="primary")
                        
                        wb_item_status = gr.Textbox(label="Status", interactive=False)
                        
                        # event handlers of the items tab
                        def update_cluster_choices(question_id):
                            if not question_id:
                                return gr.update(choices=[choice_all()], value=ALL)
                            clusters = get_clusters_for_question(question_id)
                            choices = [choice_all()] + pairs(c['name_source'] for c in clusters)
                            return gr.update(choices=choices, value=ALL)
                        
                        wb_item_question.change(
                            fn=update_cluster_choices,
                            inputs=[wb_item_question],
                            outputs=[wb_item_cluster]
                        ).then(
                            fn=load_items_table,
                            inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                            outputs=[wb_items_table]
                        )
                        
                        wb_item_cluster.change(
                            fn=load_items_table,
                            inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                            outputs=[wb_items_table]
                        )
                        
                        wb_item_search.submit(
                            fn=load_items_table,
                            inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                            outputs=[wb_items_table]
                        )
                        
                        wb_refresh_items_btn.click(
                            fn=load_items_table,
                            inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                            outputs=[wb_items_table]
                        )
                        
                        def save_item_changes(df, question_id):
                            """Changed texts, and items moved to the cluster named in the column 'Cluster'."""
                            try:
                                from services.workbench_store import save_items_table
                                return save_items_table(df, question_id)
                            except Exception as e:
                                return f"❌ Error: {e}"
                        
                        wb_save_items_btn.click(
                            fn=save_item_changes,
                            inputs=[wb_items_table, wb_item_question],
                            outputs=[wb_item_status]
                        )
                    
                    # sub-tab: normalisation
                    with gr.Tab("🔤 Normalization"):
                        gr.Markdown("#### Find and normalize similar items")
                        gr.Markdown(
                            "Finds variants of the same term (e.g. 'MS 365', 'Microsoft 365', 'M365') and unifies them.\n\n"
                            "**Tips:**\n"
                            "- Raise the **minimum similarity** (e.g. 0.9) to get fewer wrong groupings\n"
                            "- Set **Apply** to 'No' for groups you do not want to normalize\n"
                            "- Change the **suggestion** to use another canonical form"
                        )
                        
                        with gr.Row():
                            wb_norm_question = gr.Dropdown(
                                label="Question",
                                choices=[],
                                interactive=True
                            )
                            wb_similarity_slider = gr.Slider(
                                minimum=0.5,
                                maximum=1.0,
                                value=0.85,
                                step=0.05,
                                label="Minimum similarity"
                            )
                            wb_find_similar_btn = gr.Button("🔍 Find similar items", variant="primary")
                        
                        wb_similar_table = gr.Dataframe(
                            headers=SIMILAR_ITEMS.labels,
                            datatype=['number', 'str', 'number', 'str', 'str'],
                            # Gradio 6: column_count=(N, 'fixed') -> column_count + column_limits
                            column_count=5,
                            column_limits=(5, 5),
                            interactive=True,
                            label="Groups found: set 'Apply' to 'No' to skip a group"
                        )
                        
                        with gr.Row():
                            wb_apply_norm_btn = gr.Button("✓ Apply normalization", variant="primary")
                        
                        wb_norm_status = gr.Textbox(label="Status", interactive=False)
                        
                        # event handlers of the normalisation
                        wb_find_similar_btn.click(
                            fn=load_similar_items_table,
                            inputs=[wb_norm_question, wb_similarity_slider],
                            outputs=[wb_similar_table]
                        )
                        
                        def apply_normalizations_from_table(df, question_id):
                            if df is None or df.empty:
                                return "ℹ️ No groups to apply: no similar items were found"
                            if not question_id:
                                return "❌ Select a question"
                            
                            try:
                                normalizations = []
                                skipped = 0
                                for _, row in SIMILAR_ITEMS.from_ui(df).iterrows():
                                    # a group is applied unless its "apply" cell says no
                                    if str(row.get('apply', APPLY_YES)).strip().lower() in APPLY_NO:
                                        skipped += 1
                                        continue
                                    
                                    # extract the variants from the string
                                    variants_str = row['variants']
                                    # Format: '"var1" (3x), "var2" (2x)'
                                    import re
                                    variants = re.findall(r'"([^"]+)"', variants_str)
                                    canonical = row['suggestion']
                                    
                                    if variants and canonical:
                                        normalizations.append({
                                            'variants': variants,
                                            'canonical': canonical
                                        })
                                
                                if not normalizations:
                                    if skipped > 0:
                                        return f"⚠️ All {skipped} groups skipped (Apply = No)"
                                    return "❌ No normalizations found"
                                
                                count = apply_normalization(question_id, normalizations)
                                msg = f"✅ {count} items normalized"
                                if skipped > 0:
                                    msg += f" ({skipped} groups skipped)"
                                return msg
                            except Exception as e:
                                logger.exception("Normalization error")
                                return f"❌ Error: {e}"
                        
                        wb_apply_norm_btn.click(
                            fn=apply_normalizations_from_table,
                            inputs=[wb_similar_table, wb_norm_question],
                            outputs=[wb_norm_status]
                        ).then(
                            fn=lambda: state.mark_reexport_needed("Normalization applied"),
                            outputs=[],
                        )
                    
                    # sub-tab: numeric clean-up
                    with gr.Tab("🔢 Numeric"):
                        gr.Markdown("#### Check numeric questions and adjust the range")
                        gr.Markdown(
                            "Shows invalid and out-of-range answers "
                            "for a numeric question and lets you adjust "
                            "the `valid_range`. Range changes are saved in "
                            "`questions.yaml` with a snapshot."
                        )
                        
                        wb_numeric_question = gr.Dropdown(
                            label="Numeric question",
                            choices=[],
                            interactive=True,
                        )
                        
                        with gr.Row():
                            wb_numeric_refresh_btn = gr.Button(
                                "🔄 Refresh list",
                                size="sm",
                            )
                            wb_numeric_load_btn = gr.Button(
                                "🔍 Analyze question",
                                size="sm",
                                variant="primary",
                            )
                        
                        wb_numeric_overview_md = gr.Markdown(
                            "_Choose a numeric question and click "
                            "'Analyze question'._"
                        )
                        
                        gr.Markdown("##### Invalid / out-of-range values")
                        
                        wb_numeric_invalid_table = gr.Dataframe(
                            headers=["Response ID", "Raw value", "Parsed", "Status"],
                            datatype=["number", "str", "str", "str"],
                            label="Invalid values",
                            interactive=False,
                            wrap=True,
                        )
                        
                        gr.Markdown("##### Adjust the range")
                        gr.Markdown(
                            "Values are read as decimal numbers. "
                            "Empty = no limit."
                        )
                        
                        with gr.Row():
                            wb_numeric_min = gr.Textbox(
                                label="Minimum",
                                placeholder="z.B. 0",
                                interactive=True,
                            )
                            wb_numeric_max = gr.Textbox(
                                label="Maximum",
                                placeholder="z.B. 200",
                                interactive=True,
                            )
                        
                        wb_numeric_save_range_btn = gr.Button(
                            "💾 Save range (with snapshot)",
                            variant="primary",
                        )
                        
                        wb_numeric_status = gr.Textbox(
                            label="Status",
                            lines=3,
                            interactive=False,
                        )
                        
                        # ----- Numeric-Workbench-Handler -----
                        
                        def wb_numeric_refresh():
                            """Dropdown choices for all numeric questions."""
                            if state.config is None:
                                load_config()
                            
                            infos = svc_numeric_workbench.list_numeric_questions(state)
                            if not infos:
                                return (
                                    gr.Dropdown(choices=[], value=None),
                                    "⚠️ No numeric questions in the configuration."
                                )
                            choices = [info.display_choice() for info in infos]
                            return (
                                gr.Dropdown(choices=choices, value=choices[0]),
                                f"✅ {len(infos)} numeric question(s) found."
                            )
                        
                        def wb_numeric_load(choice):
                            """Loads the detail view of a question."""
                            if not choice:
                                return (
                                    "_Choose a question first._",
                                    [],
                                    "",
                                    "",
                                    "⚠️ No question selected."
                                )
                            
                            info = svc_numeric_workbench.find_question_by_choice(
                                state, choice
                            )
                            if info is None:
                                return (
                                    "_Question not found._",
                                    [],
                                    "",
                                    "",
                                    f"❌ Question for '{choice}' not found."
                                )
                            
                            overview = svc_numeric_workbench.get_numeric_overview(
                                state, info.qid
                            )
                            
                            md = svc_numeric_workbench.render_overview_summary(overview)
                            rows = svc_numeric_workbench.invalid_values_to_table_rows(
                                overview
                            )
                            
                            min_str = (str(info.min_value)
                                        if info.min_value is not None else "")
                            max_str = (str(info.max_value)
                                        if info.max_value is not None else "")
                            
                            status = (
                                f"✅ Loaded: {overview.label} — "
                                f"{overview.n_valid} valid / "
                                f"{overview.n_invalid} invalid"
                                if overview and not overview.error
                                else f"⚠️ {overview.error if overview else 'Error'}"
                            )
                            
                            return md, rows, min_str, max_str, status
                        
                        def wb_numeric_save_range(choice, min_str, max_str):
                            """Saves the changed range in questions.yaml."""
                            if not choice:
                                return (
                                    "⚠️ No question chosen.",
                                    "_No question._",
                                    [],
                                )
                            
                            info = svc_numeric_workbench.find_question_by_choice(
                                state, choice
                            )
                            if info is None:
                                return (
                                    f"❌ Question for '{choice}' not found.",
                                    "_Question not found._",
                                    [],
                                )
                            
                            def _parse_float(s):
                                s = (s or '').strip()
                                if not s:
                                    return None
                                try:
                                    return float(s.replace(',', '.'))
                                except ValueError:
                                    return s  # marker for parse errors
                            
                            min_val = _parse_float(min_str)
                            max_val = _parse_float(max_str)
                            
                            if isinstance(min_val, str):
                                return (
                                    f"❌ Minimum '{min_val}' is not a number.",
                                    "_Input error._",
                                    [],
                                )
                            if isinstance(max_val, str):
                                return (
                                    f"❌ Maximum '{max_val}' is not a number.",
                                    "_Input error._",
                                    [],
                                )
                            
                            ok, status = svc_numeric_workbench.update_valid_range(
                                state, info.qid, min_val, max_val
                            )
                            
                            if not ok:
                                return status, "_Saving failed._", []
                            
                            # mark the re-export: a range change affects the analysis
                            state.mark_reexport_needed(
                                f"Numeric range of {info.qid} changed"
                            )
                            
                            # get a fresh overview
                            overview = svc_numeric_workbench.get_numeric_overview(
                                state, info.qid
                            )
                            md = svc_numeric_workbench.render_overview_summary(overview)
                            rows = svc_numeric_workbench.invalid_values_to_table_rows(
                                overview
                            )
                            
                            return status, md, rows
                        
                        wb_numeric_refresh_btn.click(
                            fn=wb_numeric_refresh,
                            outputs=[wb_numeric_question, wb_numeric_status],
                        )
                        
                        wb_numeric_load_btn.click(
                            fn=wb_numeric_load,
                            inputs=[wb_numeric_question],
                            outputs=[
                                wb_numeric_overview_md,
                                wb_numeric_invalid_table,
                                wb_numeric_min,
                                wb_numeric_max,
                                wb_numeric_status,
                            ],
                        )
                        
                        wb_numeric_save_range_btn.click(
                            fn=wb_numeric_save_range,
                            inputs=[
                                wb_numeric_question,
                                wb_numeric_min,
                                wb_numeric_max,
                            ],
                            outputs=[
                                wb_numeric_status,
                                wb_numeric_overview_md,
                                wb_numeric_invalid_table,
                            ],
                        )
                
                # refresh handler for all Workbench dropdowns
                def refresh_workbench_questions():
                    questions = wb_get_freetext_questions()
                    logger.info(f"Workbench refresh: {len(questions)} free-text questions found")
                    if not questions:
                        return (
                            gr.update(choices=[], value=None),
                            gr.update(choices=[], value=None),
                            gr.update(choices=[], value=None),
                            "⚠️ No free-text questions found. Import data and load a configuration first."
                        )
                    return (
                        gr.update(choices=questions, value=questions[0]),
                        gr.update(choices=questions, value=questions[0]),
                        gr.update(choices=questions, value=questions[0]),
                        f"✅ {len(questions)} free-text questions loaded: {', '.join(questions)}"
                    )
                
                wb_refresh_btn.click(
                    fn=refresh_workbench_questions,
                    outputs=[wb_cluster_question, wb_item_question, wb_norm_question, wb_info]
                ).then(
                    fn=load_cluster_editor,
                    inputs=[wb_cluster_question],
                    outputs=[wb_cluster_table]
                ).then(
                    fn=load_items_table,
                    inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                    outputs=[wb_items_table]
                )
                
                def prepare_all_freetext():
                    """Runs item extraction and analysis for all free-text questions."""
                    return svc_workbench.prepare_all_freetext(state)
                
                wb_prepare_all_btn.click(
                    fn=prepare_all_freetext,
                    outputs=[wb_info]
                ).then(
                    fn=refresh_workbench_questions,
                    outputs=[wb_cluster_question, wb_item_question, wb_norm_question, wb_info]
                ).then(
                    fn=load_cluster_editor,
                    inputs=[wb_cluster_question],
                    outputs=[wb_cluster_table]
                ).then(
                    fn=load_items_table,
                    inputs=[wb_item_question, wb_item_cluster, wb_item_search],
                    outputs=[wb_items_table]
                )
                
                # ----- re-export banner handlers -----
                
                def _build_reexport_banner():
                    """
                    Returns (md_text, md_visible, reexport_btn_visible, dismiss_btn_visible)
                    based on state.pending_reexport.
                    """
                    if not state.pending_reexport:
                        return (
                            "_No re-export pending._",
                            True,
                            False,
                            False,
                        )
                    
                    reasons = state.pending_reexport_reasons or []
                    if reasons:
                        first_three = reasons[:3]
                        more = len(reasons) - 3
                        reason_text = ", ".join(first_three)
                        if more > 0:
                            reason_text += f" and {more} more change(s)"
                    else:
                        reason_text = "Workbench/theme changes"
                    
                    md = (
                        f"⚠️ **Re-export recommended**\n\n"
                        f"Changes since the last export: _{reason_text}_."
                    )
                    return md, True, True, True
                
                def workbench_banner_check():
                    md, vis, rx_vis, dm_vis = _build_reexport_banner()
                    return (
                        gr.update(value=md, visible=vis),
                        gr.update(visible=rx_vis),
                        gr.update(visible=dm_vis),
                    )
                
                def workbench_banner_reexport(progress=gr.Progress()):
                    """Starts a re-export and clears the flag on success."""
                    status, output = svc_pipeline.reexport_after_workbench(
                        state,
                        languages=None,  # the survey's output languages
                        reassign_items=True,
                        progress=_gradio_progress_callback(progress),
                    )
                    if output:   # success: the export directory (failures return "")
                        state.clear_reexport_flag()
                    md, vis, rx_vis, dm_vis = _build_reexport_banner()
                    return (
                        gr.update(value=md, visible=vis),
                        gr.update(visible=rx_vis),
                        gr.update(visible=dm_vis),
                        status,
                    )
                
                def workbench_banner_dismiss():
                    """'Later' click: the flag stays set, but the banner collapses."""
                    return (
                        gr.update(value="_Re-export postponed._", visible=True),
                        gr.update(visible=False),
                        gr.update(visible=False),
                    )
                
                workbench_banner_check_btn.click(
                    fn=workbench_banner_check,
                    outputs=[
                        workbench_banner_md,
                        workbench_banner_reexport_btn,
                        workbench_banner_dismiss_btn,
                    ],
                )
                
                workbench_banner_reexport_btn.click(
                    fn=workbench_banner_reexport,
                    outputs=[
                        workbench_banner_md,
                        workbench_banner_reexport_btn,
                        workbench_banner_dismiss_btn,
                        wb_info,
                    ],
                )
                
                workbench_banner_dismiss_btn.click(
                    fn=workbench_banner_dismiss,
                    outputs=[
                        workbench_banner_md,
                        workbench_banner_reexport_btn,
                        workbench_banner_dismiss_btn,
                    ],
                )
                
                # save operations mark the need for a re-export:
                # .then(...) is appended to the existing click handlers, without touching
                # their outputs.
                def _mark_reexport(reason):
                    state.mark_reexport_needed(reason)
                    return None
                
                wb_save_clusters_btn.click(
                    fn=lambda: _mark_reexport("Cluster changed"),
                    outputs=[],
                )
                wb_delete_cluster_btn.click(
                    fn=lambda: _mark_reexport("Cluster deleted"),
                    outputs=[],
                )
                wb_save_items_btn.click(
                    fn=lambda: _mark_reexport("Items edited"),
                    outputs=[],
                )
                wb_recluster_btn.click(
                    fn=lambda: _mark_reexport("Clusters recalculated"),
                    outputs=[],
                )
            
            # ===== TAB: EXPORT =====
            with gr.Tab("📤 Export"):
                gr.Markdown("### Export results")
                
                with gr.Row():
                    with gr.Column():
                        export_question = gr.Dropdown(
                            choices=get_question_groups(),
                            label="Question group"
                        )
                        
                        export_format = gr.CheckboxGroup(
                            choices=["Word (.docx)", "JSON", "CSV", "PNG"],
                            value=["Word (.docx)", "JSON"],
                            label="Export formats"
                        )
                        
                        export_languages = gr.CheckboxGroup(
                            choices=_UI_LANGUAGES,
                            value=_out_langs,
                            label="Languages"
                        )
                        
                        export_btn = gr.Button(
                            "📤 Export",
                            variant="primary"
                        )
                    
                    with gr.Column():
                        export_status = gr.Textbox(
                            label="Export status",
                            lines=10,
                            interactive=False
                        )
                
                export_btn.click(
                    fn=export_results,
                    inputs=[export_question, export_format, export_languages],
                    outputs=[export_status]
                )
                
                gr.Markdown("---")
                
                # === GESAMTREPORT ===
                gr.Markdown("### 📚 Create the complete export")
                gr.Markdown(
                    "Runs **all analyses** and exports all results, "
                    "**broken down by the configured segments**. "
                    "This can take several minutes, especially for free-text questions with LLM clustering."
                )
                
                with gr.Row():
                    with gr.Column():
                        report_title = gr.Textbox(
                            label="Report title",
                            value="",   # empty: the report's default title in the report language
                            placeholder="empty: default title in the report language"
                        )
                        report_lang = gr.Dropdown(
                            choices=_UI_LANGUAGES,
                            value=_src_lang,
                            label="Language"
                        )
                        report_formats = gr.CheckboxGroup(
                            choices=["Word (.docx)", "JSON", "CSV"],
                            value=["Word (.docx)", "JSON"],
                            label="Export formats"
                        )
                        force_recalculate = gr.Checkbox(
                            label="Recalculate all analyses",
                            value=False,
                            info="If off: results already calculated are loaded from the database (resume mode)"
                        )
                        full_report_btn = gr.Button(
                            "📚 Start the complete export (with segments)",
                            variant="primary"
                        )
                    
                    with gr.Column():
                        full_report_status = gr.Textbox(
                            label="Export status",
                            lines=12,
                            interactive=False
                        )
                        full_report_file = gr.Textbox(
                            label="Exported files",
                            lines=4,
                            interactive=False
                        )
                
                full_report_btn.click(
                    fn=analyze_all_and_export_segmented_report,
                    inputs=[report_lang, report_title, report_formats, force_recalculate],
                    outputs=[full_report_status, full_report_file]
                )
                
                gr.Markdown("---")
                
                # === DASHBOARD-EXPORT ===
                gr.Markdown("### 📊 Export dashboard data")
                gr.Markdown(
                    "Exports all analysis results for the **interactive dashboard**. "
                    "The dashboard is a separate application that reads these JSON files.\n\n"
                    "**Exported:**\n"
                    "- `dashboard_data.json`: all questions with results (overall and by segment)\n"
                    "- `themes.json`: theme assignments for the navigation"
                )
                
                with gr.Row():
                    with gr.Column():
                        dashboard_include_segments = gr.Checkbox(
                            label="Include analyses by segment",
                            value=True,
                            info="Break the results down by the configured segments"
                        )
                        dashboard_languages = gr.CheckboxGroup(
                            choices=_UI_LANGUAGES,
                            value=_out_langs,
                            label="Languages"
                        )
                        dashboard_export_btn = gr.Button(
                            "📊 Export dashboard data",
                            variant="primary"
                        )
                    
                    with gr.Column():
                        dashboard_export_status = gr.Textbox(
                            label="Export status",
                            lines=10,
                            interactive=False
                        )
                        dashboard_export_path = gr.Textbox(
                            label="Export directory",
                            interactive=False
                        )
                
                dashboard_export_btn.click(
                    fn=lambda: ("⏳ Starting the dashboard export...", ""),
                    outputs=[dashboard_export_status, dashboard_export_path]
                ).then(
                    fn=export_dashboard_data,
                    inputs=[dashboard_include_segments, dashboard_languages],
                    outputs=[dashboard_export_status, dashboard_export_path]
                )
                
                gr.Markdown(f"### Export directory\n`{OUTPUT_DIR}`")
                
                gr.Markdown("---")
                
                # === PUBLIKATIONSDOKUMENTE ===
                gr.Markdown("### 📄 Create publication documents")
                gr.Markdown(
                    "Creates the publication documents from Markdown source files "
                    "(for example a cover, an introduction and a method note, or the Word "
                    "report of the pipeline with a cover in front; see documents.yaml of the survey). "
                    "Texts are translated paragraph by paragraph and cached; changed paragraphs "
                    "are translated again automatically."
                )
                
                with gr.Row():
                    with gr.Column():
                        pub_languages = gr.CheckboxGroup(
                            choices=_UI_LANGUAGES,
                            value=_out_langs,
                            label="Languages"
                        )
                        
                        with gr.Row():
                            pub_build_btn = gr.Button(
                                "📄 Create all documents",
                                variant="primary"
                            )
                            pub_update_btn = gr.Button(
                                "🔄 Update missing/changed only",
                                variant="secondary"
                            )
                        
                        with gr.Accordion("Cache management", open=False):
                            pub_clear_doc = gr.Dropdown(
                                choices=[choice_all("(all)")],
                                value=ALL,
                                label="Document"
                            )
                            pub_clear_lang = gr.Dropdown(
                                choices=[choice_all("(all)")] + pairs(["fr", "en"]),
                                value=ALL,
                                label="Language"
                            )
                            pub_clear_btn = gr.Button(
                                "🗑️ Clear the translation cache",
                                variant="stop",
                                size="sm"
                            )
                    
                    with gr.Column():
                        pub_status = gr.Textbox(
                            label="Status",
                            lines=12,
                            interactive=False
                        )
                
                def run_pub_build(languages, progress=gr.Progress()):
                    return svc_publish.build_all(
                        state, languages, OUTPUT_DIR,
                        progress=_gradio_progress_callback(progress),
                    )
                
                def run_pub_update(languages, progress=gr.Progress()):
                    return svc_publish.build_update(
                        state, languages, OUTPUT_DIR,
                        progress=_gradio_progress_callback(progress),
                    )
                
                def run_pub_clear(doc_filter, lang_filter):
                    return svc_publish.clear_translations(state, doc_filter, lang_filter)
                
                pub_build_btn.click(
                    fn=run_pub_build,
                    inputs=[pub_languages],
                    outputs=[pub_status]
                )
                pub_update_btn.click(
                    fn=run_pub_update,
                    inputs=[pub_languages],
                    outputs=[pub_status]
                )
                pub_clear_btn.click(
                    fn=run_pub_clear,
                    inputs=[pub_clear_doc, pub_clear_lang],
                    outputs=[pub_status]
                )
            
            # ===== TAB: THEME ANALYSIS =====
            with gr.Tab("🏷️ Themes"):
                gr.Markdown("## Theme analysis for the dashboard")
                gr.Markdown(
                    "Extract thematic keywords and assign "
                    "questions to themes for the dashboard navigation."
                )
                
                # ----- re-export banner -----
                themes_banner_md = gr.Markdown("", visible=False)
                with gr.Row():
                    themes_banner_check_btn = gr.Button(
                        "🔄 Check re-export status",
                        scale=1,
                        size="sm",
                    )
                    themes_banner_reexport_btn = gr.Button(
                        "📤 Re-export now",
                        scale=2,
                        size="sm",
                        variant="primary",
                        visible=False,
                    )
                    themes_banner_dismiss_btn = gr.Button(
                        "Later",
                        scale=1,
                        size="sm",
                        visible=False,
                    )
                themes_banner_status = gr.Textbox(
                    label="Re-export status",
                    lines=2,
                    interactive=False,
                    visible=False,
                )
                
                # Theme Analyzer initialisieren
                llm_client = get_llm_client()
                theme_analyzer = ThemeAnalyzer(state.config or {}, llm_client, OUTPUT_DIR)
                
                # load the themes from the database (if any)
                def load_themes_from_db():
                    """Loads the stored themes from the database."""
                    svc_themes.load_themes_from_db(state, theme_analyzer)
                
                load_themes_from_db()
                
                # synchronise the themes into state.themes for the dashboard export
                def sync_themes_to_state():
                    """Synchronises ThemeAnalyzer.themes with state.themes."""
                    svc_themes.sync_themes_to_state(state, theme_analyzer)
                
                # Initial sync
                sync_themes_to_state()
                
                with gr.Row():
                    with gr.Column(scale=1):
                        gr.Markdown("### Defined themes")
                        
                        from utils.languages import localized as _localized
                        from utils.prompts import source_language as _source_language
                        
                        def get_theme_data():
                            return [
                                [t.id, _localized(t.label, _source_language(), _source_language(), default=""), len(t.question_keywords)]
                                for t in theme_analyzer.themes.values()
                            ]
                        
                        theme_list = gr.Dataframe(
                            headers=["ID", "Label", "# Keywords"],
                            value=get_theme_data(),
                            interactive=False
                        )
                        
                        with gr.Accordion("Add a theme", open=False):
                            new_theme_id = gr.Textbox(label="ID", placeholder="e.g. data_protection")
                            new_theme_label = gr.Textbox(label="Label (source language)",
                                                         info="Translated into the other output languages")
                            add_theme_btn = gr.Button("➕ Add")
                    
                    with gr.Column(scale=2):
                        gr.Markdown("### Edit a theme")
                        
                        theme_select = gr.Dropdown(
                            choices=list(theme_analyzer.themes.keys()),
                            label="Choose a theme"
                        )
                        
                        theme_keywords = gr.Textbox(
                            label="Keywords (free text, separated by commas)",
                            lines=3
                        )
                        
                        with gr.Row():
                            save_theme_btn = gr.Button("💾 Save keywords")
                            extract_keywords_btn = gr.Button("🤖 Extract keywords with the LLM")
                        
                        theme_status = gr.Textbox(label="Status", lines=8, interactive=False)
                
                gr.Markdown("### Analyze all themes")
                gr.Markdown("*Analyzes all themes, extracts keywords and assigns questions.*")
                
                with gr.Row():
                    with gr.Column():
                        analyze_all_btn = gr.Button("🔍 Quick analysis (keyword matching)")
                        gr.Markdown("*1 LLM call per theme, string matching*")
                    with gr.Column():
                        deep_analyze_btn = gr.Button("🔬 Deep analysis (cluster check)", variant="primary")
                        gr.Markdown("*Many LLM calls, checks every cluster*")
                
                analyze_all_status = gr.Textbox(label="Analysis status", lines=10, interactive=False)
                
                gr.Markdown("### Export themes")
                
                with gr.Row():
                    export_themes_btn = gr.Button("📤 Export themes as JSON")
                    themes_export_status = gr.Textbox(label="Export status", interactive=False)
                
                # event handlers of the themes tab
                def add_theme_handler(theme_id, label):
                    ok, status, new_data, new_choices = svc_themes.add_theme(
                        state, theme_analyzer, theme_id, label)
                    if not ok:
                        # empty updates on errors
                        return gr.update(), gr.update(), status
                    return (
                        gr.update(value=new_data),
                        gr.update(choices=new_choices),
                        status,
                    )
                
                add_theme_btn.click(
                    fn=add_theme_handler,
                    inputs=[new_theme_id, new_theme_label],
                    outputs=[theme_list, theme_select, theme_status]
                )
                
                def load_theme_keywords(theme_id):
                    return svc_themes.get_theme_keywords(theme_analyzer, theme_id)
                
                theme_select.change(
                    fn=load_theme_keywords,
                    inputs=[theme_select],
                    outputs=[theme_keywords]
                )
                
                def save_theme_keywords(theme_id, keywords_str):
                    new_data, status = svc_themes.save_theme_keywords(
                        state, theme_analyzer, theme_id, keywords_str,
                    )
                    if not new_data:
                        return gr.update(), status
                    return gr.update(value=new_data), status
                
                save_theme_btn.click(
                    fn=save_theme_keywords,
                    inputs=[theme_select, theme_keywords],
                    outputs=[theme_list, theme_status]
                )
                
                def extract_keywords_with_llm(theme_id):
                    new_data, keywords_str, status = svc_themes.extract_keywords_with_llm(
                        state, theme_analyzer, theme_id,
                    )
                    if new_data is None:
                        return gr.update(), gr.update(), status
                    return (
                        gr.update(value=new_data),
                        gr.update(value=keywords_str),
                        status,
                    )
                
                extract_keywords_btn.click(
                    fn=extract_keywords_with_llm,
                    inputs=[theme_select],
                    outputs=[theme_list, theme_keywords, theme_status]
                )
                
                def analyze_all_themes_handler(progress=gr.Progress()):
                    """Analyses all themes and assigns questions."""
                    new_data, status = svc_themes.analyze_all_themes(
                        state, theme_analyzer,
                        progress=_gradio_progress_callback(progress),
                    )
                    if not new_data:
                        return gr.update(), status
                    return gr.update(value=new_data), status
                
                analyze_all_btn.click(
                    fn=analyze_all_themes_handler,
                    outputs=[theme_list, analyze_all_status]
                )
                
                def deep_analyze_all_themes_handler(progress=gr.Progress()):
                    """Deep analysis of all themes, the LLM checks every cluster."""
                    new_data, status = svc_themes.deep_analyze_all_themes(
                        state, theme_analyzer,
                        progress=_gradio_progress_callback(progress),
                    )
                    if not new_data:
                        return gr.update(), status
                    return gr.update(value=new_data), status
                
                deep_analyze_btn.click(
                    fn=deep_analyze_all_themes_handler,
                    outputs=[theme_list, analyze_all_status]
                )
                
                def export_themes_handler():
                    return svc_themes.export_themes(theme_analyzer)
                
                export_themes_btn.click(
                    fn=export_themes_handler,
                    outputs=[themes_export_status]
                )
                
                # ----- re-export banner handlers -----
                
                def themes_banner_check():
                    if not state.pending_reexport:
                        return (
                            gr.update(value="_No re-export pending._", visible=True),
                            gr.update(visible=False),
                            gr.update(visible=False),
                            gr.update(visible=False),
                        )
                    
                    reasons = state.pending_reexport_reasons or []
                    if reasons:
                        first_three = reasons[:3]
                        more = len(reasons) - 3
                        reason_text = ", ".join(first_three)
                        if more > 0:
                            reason_text += f" and {more} more change(s)"
                    else:
                        reason_text = "Workbench/theme changes"
                    
                    md = (
                        f"⚠️ **Re-export recommended**\n\n"
                        f"Changes since the last export: _{reason_text}_."
                    )
                    return (
                        gr.update(value=md, visible=True),
                        gr.update(visible=True),
                        gr.update(visible=True),
                        gr.update(visible=False),
                    )
                
                def themes_banner_reexport(progress=gr.Progress()):
                    status, _output = svc_pipeline.reexport_after_workbench(
                        state,
                        languages=None,  # the survey's output languages
                        reassign_items=True,
                        progress=_gradio_progress_callback(progress),
                    )
                    if _output:   # success: the export directory (failures return "")
                        state.clear_reexport_flag()
                    md_update, rx_update, dm_update, _ = themes_banner_check()
                    return (
                        md_update,
                        rx_update,
                        dm_update,
                        gr.update(value=status, visible=True),
                    )
                
                def themes_banner_dismiss():
                    return (
                        gr.update(value="_Re-export postponed._", visible=True),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(visible=False),
                    )
                
                themes_banner_check_btn.click(
                    fn=themes_banner_check,
                    outputs=[
                        themes_banner_md,
                        themes_banner_reexport_btn,
                        themes_banner_dismiss_btn,
                        themes_banner_status,
                    ],
                )
                
                themes_banner_reexport_btn.click(
                    fn=themes_banner_reexport,
                    outputs=[
                        themes_banner_md,
                        themes_banner_reexport_btn,
                        themes_banner_dismiss_btn,
                        themes_banner_status,
                    ],
                )
                
                themes_banner_dismiss_btn.click(
                    fn=themes_banner_dismiss,
                    outputs=[
                        themes_banner_md,
                        themes_banner_reexport_btn,
                        themes_banner_dismiss_btn,
                        themes_banner_status,
                    ],
                )
                
                # save operations of the themes mark the re-export
                add_theme_btn.click(
                    fn=lambda: (state.mark_reexport_needed("Theme added"), None)[1],
                    outputs=[],
                )
                save_theme_btn.click(
                    fn=lambda: (state.mark_reexport_needed("Theme keywords changed"), None)[1],
                    outputs=[],
                )
                analyze_all_btn.click(
                    fn=lambda: (state.mark_reexport_needed("Theme analysis run"), None)[1],
                    outputs=[],
                )
            
            # ===== TAB: SETUP =====
            # tab for rarely used configuration:
            # - survey selection (switch between registered surveys)
            # - segmentation dimension editor (create/edit/delete)
            # - notes on further configuration tabs
            #
            # The YAML editor, item extraction and translations stay in their own
            # top-level tabs: one tab per tool, as each has complex interface logic.
            with gr.Tab("⚙️ Setup"):
                gr.Markdown("### Setup")
                gr.Markdown(
                    "Configuration tools outside the daily work. "
                    "Switch between several installed surveys here."
                )
                
                # ----- sub-tab: survey selection -----
                with gr.Tabs():
                    with gr.Tab("🗂️ Survey selection"):
                        gr.Markdown("#### Switch the active survey")
                        gr.Markdown(
                            "Switching reloads the configuration. "
                            "Imported data and analysis results in memory are "
                            "**reset**; the data in the database is kept and can "
                            "be loaded again in the Import tab."
                        )
                        
                        survey_active_md = gr.Markdown(
                            "_Loaded when the tab opens..._",
                            label="Active survey",
                        )
                        
                        survey_list_md = gr.Markdown(
                            "_Loading the survey list..._",
                            label="Available surveys",
                        )
                        
                        # imports per survey
                        survey_imports_md = gr.Markdown(
                            "_Loading import statistics..._",
                            label="Imports per survey",
                        )
                        
                        survey_choice_dropdown = gr.Dropdown(
                            choices=[],
                            label="Choose a survey",
                            interactive=True,
                        )
                        
                        with gr.Row():
                            survey_refresh_btn = gr.Button(
                                "🔄 Refresh list"
                            )
                            survey_switch_btn = gr.Button(
                                "🔁 Switch to the chosen survey",
                                variant="primary",
                            )
                        
                        survey_switch_status = gr.Textbox(
                            label="Status",
                            lines=3,
                            interactive=False,
                        )

                        with gr.Accordion("➕ Create a new survey", open=False):
                            gr.Markdown(
                                "Creates `surveys/<id>/` with `survey.yaml` and `questions.yaml` and makes it "
                                "the active survey. The questions come from the first import: from the "
                                "structure file of the survey tool (LimeSurvey `.lss`, Qualtrics `.qsf`, "
                                "SPSS `.sav`), or inferred from the data."
                            )
                            with gr.Row():
                                new_survey_id = gr.Textbox(label="Id (lower-case letters, digits, _)",
                                                           placeholder="staff_survey_2026")
                                new_survey_title = gr.Textbox(label="Title", placeholder="Staff survey 2026")
                            with gr.Row():
                                new_survey_source = gr.Dropdown(choices=[("English", "en"), ("German", "de")],
                                                                value="en", label="Language of the answers (source language)")
                                new_survey_languages = gr.CheckboxGroup(
                                    choices=[("German", "de"), ("English", "en"), ("French", "fr"), ("Italian", "it")],
                                    value=[], label="Further output languages")
                            new_survey_btn = gr.Button("➕ Create and switch to it", variant="primary")
                        
                        # ----- Survey-Handler -----
                        
                        def _build_survey_overview():
                            """Returns (active_md, list_md, choices)."""
                            try:
                                from core import get_registry, get_active_survey_id
                                registry = get_registry()
                                active_id = get_active_survey_id()
                                surveys = registry.list_surveys()
                            except Exception as e:
                                return (
                                    f"❌ Error reading the registry: {e}",
                                    "_No surveys available._",
                                    [],
                                )
                            
                            if active_id:
                                active_meta = registry.get_survey(active_id)
                                if active_meta:
                                    active_md = (
                                        f"**Active survey:** {active_meta.get_title('de')} "
                                        f"(`{active_id}`, version {active_meta.version})"
                                    )
                                else:
                                    active_md = f"**Active survey:** `{active_id}`"
                            else:
                                active_md = "_No active survey set._"
                            
                            if not surveys:
                                list_md = "_No surveys registered._"
                                choices = []
                            else:
                                lines = ["**Available surveys:**", ""]
                                for s in surveys:
                                    marker = "● " if s.id == active_id else "○ "
                                    lines.append(
                                        f"- {marker}{s.get_title('de')} "
                                        f"(`{s.id}`, v{s.version})"
                                    )
                                list_md = "\n".join(lines)
                                choices = [
                                    f"{s.id} — {s.get_title('de')}"
                                    for s in surveys
                                ]
                            
                            return active_md, list_md, choices
                        
                        def refresh_survey_overview():
                            active_md, list_md, choices = _build_survey_overview()
                            imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                            return (
                                active_md,
                                list_md,
                                imports_md,
                                gr.Dropdown(choices=choices),
                            )
                        
                        def switch_active_survey(choice):
                            """Activates the chosen survey and resets the state."""
                            if not choice:
                                active_md, list_md, _ = _build_survey_overview()
                                imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                                return (
                                    "⚠️ Choose a survey from the list first.",
                                    active_md, list_md, imports_md,
                                )
                            
                            # Format: "<id> — <title>"
                            survey_id = choice.split(" — ")[0].strip()
                            
                            try:
                                from core import set_active_survey
                                set_active_survey(survey_id)
                            except KeyError as e:
                                active_md, list_md, _ = _build_survey_overview()
                                imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                                return (
                                    f"❌ Survey not found: {e}",
                                    active_md, list_md, imports_md,
                                )
                            except Exception as e:
                                active_md, list_md, _ = _build_survey_overview()
                                imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                                return (
                                    f"❌ Error switching: {e}",
                                    active_md, list_md, imports_md,
                                )
                            
                            # reset the state: the loaded data belongs to the old survey
                            # (the project stays: it holds the imports of all surveys, separated by survey_id)
                            state.df = None
                            state.import_id = None
                            state.config = None
                            state.analysis_results = {}
                            state.themes = {}
                            
                            # load the configuration of the new survey
                            try:
                                load_config()
                            except Exception as e:
                                active_md, list_md, _ = _build_survey_overview()
                                imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                                return (
                                    f"⚠️ Survey switched, but the configuration "
                                    f"could not be loaded: {e}",
                                    active_md, list_md, imports_md,
                                )
                            
                            active_md, list_md, _ = _build_survey_overview()
                            imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                            status = (
                                f"✅ The active survey is now `{survey_id}`. "
                                f"Configuration reloaded, data in memory reset. "
                                f"Load the data again in the Import tab."
                            )
                            return status, active_md, list_md, imports_md
                        
                        survey_refresh_btn.click(
                            fn=refresh_survey_overview,
                            outputs=[
                                survey_active_md,
                                survey_list_md,
                                survey_imports_md,
                                survey_choice_dropdown,
                            ],
                        )
                        
                        # Language controls follow the active survey (source language
                        # for single choices, output languages for exports)
                        _language_controls = [translate_target_lang, lang_dropdown, report_lang,
                                              pipeline_languages, export_languages, dashboard_languages]

                        def _language_control_updates():
                            src, out = _survey_language_defaults()
                            return ([gr.update(value=src)] * 3) + ([gr.update(value=out)] * 3)

                        survey_switch_btn.click(
                            fn=switch_active_survey,
                            inputs=[survey_choice_dropdown],
                            outputs=[
                                survey_switch_status,
                                survey_active_md,
                                survey_list_md,
                                survey_imports_md,
                            ],
                        ).then(
                            fn=_language_control_updates,
                            outputs=_language_controls,
                        ).then(
                            # the files chosen for the previous survey are not imported into this one
                            fn=lambda: (None, None),
                            outputs=[file_input, lss_input],
                        )

                        def create_new_survey(survey_id, title, source_language, languages):
                            """Creates the survey and switches to it, as the switch button does."""
                            from services.surveys import create_survey
                            ok, message = create_survey(survey_id, title, source_language, languages)
                            if not ok:
                                active_md, list_md, choices = _build_survey_overview()
                                imports_md = svc_survey_status.render_imports_per_survey_markdown(state)
                                return (message, active_md, list_md, imports_md, gr.Dropdown(choices=choices),
                                        gr.update(), gr.update(), gr.update())
                            status, active_md, list_md, imports_md = switch_active_survey(f"{survey_id.strip()} — ")
                            _, _, choices = _build_survey_overview()
                            # the form is emptied, so that the next survey does not inherit these values
                            return (f"{message}\n{status}", active_md, list_md, imports_md,
                                    gr.Dropdown(choices=choices), "", "", [])

                        new_survey_btn.click(
                            fn=create_new_survey,
                            inputs=[new_survey_id, new_survey_title, new_survey_source, new_survey_languages],
                            outputs=[survey_switch_status, survey_active_md, survey_list_md,
                                     survey_imports_md, survey_choice_dropdown,
                                     new_survey_id, new_survey_title, new_survey_languages],
                        ).then(
                            fn=_language_control_updates,
                            outputs=_language_controls,
                        ).then(
                            # the files chosen for the previous survey are not imported into this one
                            fn=lambda: (None, None),
                            outputs=[file_input, lss_input],
                        )
                        
                        # load the content once at start
                        # (not in on_load, but directly at component initialisation)
                        try:
                            _initial_active_md, _initial_list_md, _initial_choices = (
                                _build_survey_overview()
                            )
                            survey_active_md.value = _initial_active_md
                            survey_list_md.value = _initial_list_md
                            # (label, value) pairs: an assignment after construction is not
                            # normalised by Gradio, plain strings showed only their first letter
                            survey_choice_dropdown.choices = [(c, c) for c in _initial_choices]
                            survey_imports_md.value = (
                                svc_survey_status.render_imports_per_survey_markdown(state)
                            )
                        except Exception as e:
                            logger.warning(f"Could not load the survey selection initially: {e}")
                    
                    # ----- sub-tab: segmentation dimensions -----
                    with gr.Tab("🧭 Segmentation dimensions"):
                        gr.Markdown("#### Maintain segmentation dimensions")
                        gr.Markdown(
                            "Define by which answers the results can be "
                            "grouped. Each dimension refers to "
                            "a question and a bucketing strategy. "
                            "Changes are saved in `questions.yaml` with a snapshot."
                        )
                        
                        # ----- list of all dimensions -----
                        gr.Markdown("##### Current dimensions")
                        
                        seg_dim_table = gr.Dataframe(
                            headers=["ID", "Label", "Source question", "Bucketing"],
                            datatype=["str", "str", "str", "str"],
                            label="Configured dimensions",
                            interactive=False,
                            wrap=True,
                        )
                        
                        with gr.Row():
                            seg_refresh_btn = gr.Button("🔄 Refresh list", size="sm")
                            seg_new_btn     = gr.Button("➕ New dimension", size="sm",
                                                          variant="primary")
                        
                        gr.Markdown("---")
                        gr.Markdown("##### Editor")
                        
                        seg_edit_mode = gr.Radio(
                            choices=[("Create new", "new"), ("Edit existing", "edit")],
                            value="new",
                            label="Mode",
                        )
                        
                        seg_existing_select = gr.Dropdown(
                            choices=[],
                            label="Choose an existing dimension",
                            interactive=True,
                            visible=False,
                        )
                        
                        with gr.Row():
                            seg_edit_id = gr.Textbox(
                                label="ID",
                                placeholder="e.g. region, faculty",
                            )
                            seg_edit_label = gr.Textbox(
                                label="Label (source language of the survey)",
                                placeholder="e.g. Role",
                            )
                        
                        seg_edit_source = gr.Dropdown(
                            choices=[],
                            label="Source question (qid)",
                            interactive=True,
                        )
                        
                        seg_edit_bucketing_type = gr.Radio(
                            choices=[
                                ("identity (answer = bucket)", "identity"),
                                ("value_mapping (raw value → bucket)", "value_mapping"),
                                ("numeric_buckets (number ranges)", "numeric_buckets"),
                            ],
                            value="identity",
                            label="Bucketing strategy",
                        )
                        
                        # value mapping table (visible only with value_mapping)
                        seg_edit_mapping_table = gr.Dataframe(
                            headers=["Raw value", "Bucket label"],
                            datatype=["str", "str"],
                            label="Value mapping",
                            interactive=True,
                            visible=False,
                            row_count=(1, "dynamic"),
                            # Gradio 6: col_count=(2, "fixed") -> column_count + column_limits
                            column_count=2,
                            column_limits=(2, 2),
                        )
                        
                        # numeric buckets table
                        seg_edit_buckets_table = gr.Dataframe(
                            headers=["Bucket label", "Min (≥)", "Max (<)"],
                            datatype=["str", "number", "number"],
                            label="Numeric buckets",
                            interactive=True,
                            visible=False,
                            row_count=(1, "dynamic"),
                            # Gradio 6: col_count=(3, "fixed") -> column_count + column_limits
                            column_count=3,
                            column_limits=(3, 3),
                        )
                        
                        seg_edit_source_column = gr.Textbox(
                            label="… or source column (data column without its own question)",
                            placeholder="leave empty if a question is chosen above",
                        )
                        
                        with gr.Accordion("Free text: segment-specific clusters", open=False):
                            seg_edit_specific = gr.Checkbox(
                                label="Cluster the free-text answers of each segment separately for this dimension",
                                value=False,
                            )
                            seg_edit_specific_min = gr.Number(
                                label="Minimum size per segment (empty = default of the survey)",
                                value=None, precision=0,
                            )
                        
                        with gr.Accordion("Advanced: handling of unmapped values", open=False):
                            seg_edit_unmapped_type = gr.Radio(
                                choices=[
                                    ("passthrough (keep unchanged)", "passthrough"),
                                    ("drop (discard)", "drop"),
                                    ("bucket (into a collecting bucket)", "bucket"),
                                ],
                                value="passthrough",
                                label="Strategy",
                            )
                            seg_edit_unmapped_label = gr.Textbox(
                                label="Label of the collecting bucket (only with 'bucket')",
                                placeholder="e.g. Other",
                                visible=False,
                            )
                        
                        with gr.Row():
                            seg_save_btn = gr.Button(
                                "💾 Save (with snapshot)",
                                variant="primary",
                            )
                            seg_delete_btn = gr.Button(
                                "🗑️ Delete this dimension",
                                visible=False,
                            )
                        
                        seg_edit_status = gr.Textbox(
                            label="Status",
                            lines=4,
                            interactive=False,
                        )
                        
                        gr.Markdown("---")
                        gr.Markdown("##### Distribution preview")
                        gr.Markdown(
                            "Shows how the answers imported now "
                            "are distributed over the buckets of this dimension."
                        )
                        
                        seg_preview_btn = gr.Button(
                            "📊 Calculate distribution",
                            size="sm",
                        )
                        seg_preview_table = gr.Dataframe(
                            headers=["Bucket", "Count"],
                            datatype=["str", "number"],
                            label="Distribution",
                            interactive=False,
                        )
                        seg_preview_status = gr.Textbox(
                            label="Status",
                            lines=2,
                            interactive=False,
                        )
                        
                        gr.Markdown("---")
                        gr.Markdown("##### Cross filters and thresholds")
                        gr.Markdown(
                            "Cross filters combine 2 or 3 dimensions already defined. "
                            "Groups below the minimum size are not exported."
                        )
                        seg_cross_filters = gr.CheckboxGroup(choices=[], label="Cross filters")
                        with gr.Row():
                            seg_specific_default = gr.Number(
                                label="Minimum size of segment-specific clusters (default)", value=15, precision=0)
                            seg_cross_min = gr.Number(
                                label="Minimum size of cross-filter groups", value=5, precision=0)
                        seg_settings_save_btn = gr.Button("💾 Save cross filters and thresholds")
                        seg_settings_status = gr.Textbox(label="Status", interactive=False)
                        
                        # =========================================================
                        # Handler
                        # =========================================================
                        
                        def seg_refresh():
                            """Updates the list, the source dropdown and the existing dropdown."""
                            if state.config is None:
                                load_config()
                            
                            summaries = svc_segmentation_editor.list_dimensions(state)
                            rows = svc_segmentation_editor.render_dimension_table(summaries)
                            
                            qids = svc_segmentation_editor.get_question_choices_for_dropdown(state)
                            existing_ids = [s.id for s in summaries]
                            
                            return (
                                rows,
                                gr.Dropdown(choices=qids),
                                gr.Dropdown(choices=existing_ids),
                            )
                        
                        def seg_on_mode_change(mode):
                            """Controls the visibility of the existing-dimension choice."""
                            return (
                                gr.update(visible=(mode == "edit")),
                                gr.update(visible=(mode == "edit")),
                            )
                        
                        def seg_on_bucketing_type_change(btype):
                            """Controls the visibility of the mapping/buckets tables."""
                            return (
                                gr.update(visible=(btype == "value_mapping")),
                                gr.update(visible=(btype == "numeric_buckets")),
                            )
                        
                        def seg_on_unmapped_type_change(utype):
                            return gr.update(visible=(utype == "bucket"))
                        
                        def seg_load_existing(dim_id):
                            """Loads an existing dimension into the editor fields."""
                            if not dim_id:
                                return ("", "", gr.update(value=None),
                                        "identity", [], [],
                                        "passthrough", "",
                                        "ℹ️ No dimension chosen.", "", False, None)
                            
                            detail = svc_segmentation_editor.get_dimension_detail(state, dim_id)
                            if detail is None:
                                return ("", "", gr.update(value=None),
                                        "identity", [], [],
                                        "passthrough", "",
                                        f"❌ Dimension '{dim_id}' not found.", "", False, None)
                            
                            label_source = (detail.label.get(detail.source_language, '')
                                        if isinstance(detail.label, dict)
                                        else str(detail.label))
                            mapping_rows = (
                                svc_segmentation_editor.value_mapping_to_table(
                                    detail.value_mapping
                                )
                                if detail.bucketing_type == 'value_mapping' else []
                            )
                            bucket_rows = (
                                svc_segmentation_editor.numeric_buckets_to_table(
                                    detail.numeric_buckets
                                )
                                if detail.bucketing_type == 'numeric_buckets' else []
                            )
                            
                            return (
                                detail.id,
                                label_source,
                                gr.update(value=detail.source_question),
                                detail.bucketing_type,
                                mapping_rows,
                                bucket_rows,
                                detail.unmapped_type,
                                detail.unmapped_bucket_label,
                                f"✅ Dimension '{dim_id}' loaded.",
                                detail.source_column,
                                detail.enable_specific_clusters,
                                detail.specific_clusters_min_n,
                            )
                        
                        def seg_save(mode, dim_id, label_source, source_qid,
                                      bucketing_type, mapping_table, buckets_table,
                                      unmapped_type, unmapped_label,
                                      source_column, specific, specific_min):
                            """
                            Saves the dimension. When editing, the stored dimension is
                            the starting point, so settings the form does not show
                            (order, description, value_labels, labels in other
                            languages) are kept.
                            """
                            from services.segmentation_editor import (
                                DimensionDetail,
                                value_mapping_from_table,
                                numeric_buckets_from_table,
                                _source_language,
                            )
                            
                            src = _source_language(state)
                            dim_id = (dim_id or '').strip()
                            existing = (svc_segmentation_editor.get_dimension_detail(state, dim_id)
                                        if mode == 'edit' else None)
                            detail = existing or DimensionDetail(id=dim_id, source_language=src)
                            detail.id = dim_id
                            detail.label = {**(existing.label if existing else {}),
                                            src: (label_source or '').strip()}
                            detail.source_column = (source_column or '').strip()
                            detail.source_question = '' if detail.source_column else (source_qid or '')
                            detail.bucketing_type = bucketing_type or 'identity'
                            detail.unmapped_type = unmapped_type or 'passthrough'
                            detail.unmapped_bucket_label = (unmapped_label or '').strip()
                            detail.enable_specific_clusters = bool(specific)
                            detail.specific_clusters_min_n = (int(specific_min)
                                                              if specific_min not in (None, '') else None)
                            
                            if bucketing_type == 'value_mapping':
                                detail.value_mapping = value_mapping_from_table(mapping_table)
                            elif bucketing_type == 'numeric_buckets':
                                new_buckets = numeric_buckets_from_table(buckets_table)
                                # keep labels in other languages, matched by position
                                old_buckets = existing.numeric_buckets if existing else []
                                for i, nb in enumerate(new_buckets):
                                    if i < len(old_buckets):
                                        nb['label_all'] = old_buckets[i].get('label_all', {})
                                detail.numeric_buckets = new_buckets
                            
                            ok, status = svc_segmentation_editor.save_dimension(
                                state, detail, is_new=(mode == 'new'),
                            )
                            
                            # reload the list
                            summaries = svc_segmentation_editor.list_dimensions(state)
                            rows = svc_segmentation_editor.render_dimension_table(summaries)
                            
                            return status, rows
                        
                        def seg_settings_load():
                            """Cross filter choice and thresholds from the configuration."""
                            cfg = svc_segmentation_editor.get_segmentation_settings(state)
                            choices = svc_segmentation_editor.cross_filter_choices(state)
                            selected = [" + ".join(c) for c in cfg['cross_filters']]
                            return (gr.update(choices=choices, value=[c for c in selected if c in choices]),
                                    cfg['specific_clusters_min_n'], cfg['cross_filter_min_n'])
                        
                        def seg_settings_save(selected, specific_default, cross_min):
                            ok, status = svc_segmentation_editor.save_segmentation_settings(
                                state, selected, specific_default, cross_min)
                            return status
                        
                        def seg_delete(dim_id):
                            """Deletes a dimension."""
                            if not dim_id:
                                summaries = svc_segmentation_editor.list_dimensions(state)
                                return ("⚠️ No dimension chosen.",
                                        svc_segmentation_editor.render_dimension_table(summaries))
                            
                            ok, status = svc_segmentation_editor.delete_dimension(
                                state, dim_id
                            )
                            
                            summaries = svc_segmentation_editor.list_dimensions(state)
                            rows = svc_segmentation_editor.render_dimension_table(summaries)
                            return status, rows
                        
                        def seg_preview(mode, dim_id):
                            """Shows the distribution of the dimension on the current data."""
                            target_id = dim_id if mode == 'edit' else None
                            
                            if not target_id:
                                return [], "ℹ️ Choose an existing dimension."
                            
                            pv = svc_segmentation_editor.preview_distribution(state, target_id)
                            
                            if pv.error:
                                return [], f"⚠️ {pv.error}"
                            
                            rows = [[label, n] for label, n in pv.rows]
                            status_text = (
                                f"✅ {pv.n_total} answers distributed over "
                                f"{len(pv.rows)} bucket(s)."
                            )
                            if pv.n_unmapped > 0:
                                status_text += (
                                    f" ⚠️ {pv.n_unmapped} unmapped values "
                                    f"(strategy 'passthrough'/raw values)."
                                )
                            
                            return rows, status_text
                        
                        # =========================================================
                        # Click-Bindings
                        # =========================================================
                        
                        seg_refresh_btn.click(
                            fn=seg_refresh,
                            outputs=[seg_dim_table, seg_edit_source, seg_existing_select],
                        ).then(
                            fn=seg_settings_load,
                            outputs=[seg_cross_filters, seg_specific_default, seg_cross_min],
                        )
                        
                        seg_settings_save_btn.click(
                            fn=seg_settings_save,
                            inputs=[seg_cross_filters, seg_specific_default, seg_cross_min],
                            outputs=[seg_settings_status],
                        )
                        
                        seg_new_btn.click(
                            fn=lambda: ("new", "", "", gr.update(value=None),
                                         "identity",
                                         [["", ""]],     # mapping table: 1 empty row
                                         [["", None, None]],  # buckets table: 1 empty row
                                         "passthrough", "",
                                         "Ready for a new dimension. "
                                         "Choose a bucketing type and fill in the fields.",
                                         "", False, None),
                            outputs=[
                                seg_edit_mode, seg_edit_id, seg_edit_label,
                                seg_edit_source, seg_edit_bucketing_type,
                                seg_edit_mapping_table, seg_edit_buckets_table,
                                seg_edit_unmapped_type, seg_edit_unmapped_label,
                                seg_edit_status,
                                seg_edit_source_column, seg_edit_specific, seg_edit_specific_min,
                            ],
                        )
                        
                        seg_edit_mode.change(
                            fn=seg_on_mode_change,
                            inputs=[seg_edit_mode],
                            outputs=[seg_existing_select, seg_delete_btn],
                        )
                        
                        seg_edit_bucketing_type.change(
                            fn=seg_on_bucketing_type_change,
                            inputs=[seg_edit_bucketing_type],
                            outputs=[seg_edit_mapping_table, seg_edit_buckets_table],
                        )
                        
                        seg_edit_unmapped_type.change(
                            fn=seg_on_unmapped_type_change,
                            inputs=[seg_edit_unmapped_type],
                            outputs=[seg_edit_unmapped_label],
                        )
                        
                        seg_existing_select.change(
                            fn=seg_load_existing,
                            inputs=[seg_existing_select],
                            outputs=[
                                seg_edit_id, seg_edit_label, seg_edit_source,
                                seg_edit_bucketing_type,
                                seg_edit_mapping_table, seg_edit_buckets_table,
                                seg_edit_unmapped_type, seg_edit_unmapped_label,
                                seg_edit_status,
                                seg_edit_source_column, seg_edit_specific, seg_edit_specific_min,
                            ],
                        )
                        
                        seg_save_btn.click(
                            fn=seg_save,
                            inputs=[
                                seg_edit_mode, seg_edit_id, seg_edit_label,
                                seg_edit_source, seg_edit_bucketing_type,
                                seg_edit_mapping_table, seg_edit_buckets_table,
                                seg_edit_unmapped_type, seg_edit_unmapped_label,
                                seg_edit_source_column, seg_edit_specific, seg_edit_specific_min,
                            ],
                            outputs=[seg_edit_status, seg_dim_table],
                        )
                        
                        seg_delete_btn.click(
                            fn=seg_delete,
                            inputs=[seg_edit_id],
                            outputs=[seg_edit_status, seg_dim_table],
                        )
                        
                        seg_preview_btn.click(
                            fn=seg_preview,
                            inputs=[seg_edit_mode, seg_edit_id],
                            outputs=[seg_preview_table, seg_preview_status],
                        )
                        
                        # initial load: source dropdown, dimension table, existing dropdown.
                        # If state.config is not loaded yet (e.g. at start without an explicit
                        # survey switch), load_config() is tried.
                        try:
                            if state.config is None:
                                try:
                                    load_config()
                                except Exception as e:
                                    logger.warning(
                                        f"Could not load the configuration for the segmentation editor "
                                        f"initially: {e}"
                                    )
                            
                            _initial_summaries = svc_segmentation_editor.list_dimensions(state)
                            seg_dim_table.value = svc_segmentation_editor.render_dimension_table(
                                _initial_summaries
                            )
                            _initial_qids = svc_segmentation_editor.get_question_choices_for_dropdown(state)
                            # (label, value) pairs, see the survey selection above
                            seg_edit_source.choices = [(q, q) for q in _initial_qids]
                            seg_existing_select.choices = [(s.id, s.id) for s in _initial_summaries]
                            _seg_cfg = svc_segmentation_editor.get_segmentation_settings(state)
                            _cf_choices = svc_segmentation_editor.cross_filter_choices(state)
                            seg_cross_filters.choices = [(c, c) for c in _cf_choices]
                            seg_cross_filters.value = [" + ".join(c) for c in _seg_cfg['cross_filters']
                                                       if " + ".join(c) in _cf_choices]
                            seg_specific_default.value = _seg_cfg['specific_clusters_min_n']
                            seg_cross_min.value = _seg_cfg['cross_filter_min_n']
                            
                            logger.info(
                                f"Segmentation editor initially: "
                                f"{len(_initial_summaries)} dimensions, "
                                f"{len(_initial_qids)} questions available"
                            )
                        except Exception as e:
                            logger.warning(f"Could not load the segmentation editor initially: {e}")
                    
                    with gr.Tab("ℹ️ Notes"):
                        gr.Markdown("""
                        #### More configuration tools
                        
                        Not every configuration task is in the Setup tab.
                        Each tool has its own top-level tab:
                        
                        - **🛠️ Config assistant**: LLM suggestions
                          for `llm_context`, segmentation dimensions,
                          numeric settings and schema overrides, with an automatic
                          snapshot
                        - **⚙️ Configuration**: direct YAML editor (power users)
                          for the `questions.yaml` of the active survey
                        - **🔧 Item extraction**: testing and maintenance
                          of free-text items
                        - **📥 Import**: data import, translation tables
                        
                        In Setup itself:
                        
                        - **🗂️ Survey selection**: switch the active survey
                          (resets the data in memory)
                        - **🧭 Segmentation dimensions**: create, edit and delete
                          the `settings.segmentation.dimensions`, with a
                          distribution preview
                        """)
            
            # ===== TAB: INFO =====
            with gr.Tab("ℹ️ Info"):
                gr.Markdown("""
                ## Survey Analysis Suite
                
                Analyzes surveys from LimeSurvey, Qualtrics, SPSS and
                CSV/Excel with configurable question handlers,
                segmentation and LLM support.
                
                ### Tabs at a glance
                
                | Tab | Purpose |
                |---|---|
                | 🏠 Overview | Workflow status, next sensible step |
                | 🛠️ Config assistant | LLM configuration suggestions with snapshot |
                | 📥 Import | CSV/Excel/SPSS/LSS/QSF import, translation tables |
                | ▶️ Pipeline | End-to-end analysis (item extraction, analysis, export) |
                | ⚙️ Configuration | Direct YAML editor |
                | 🔧 Item extraction | Split free text into single items |
                | 🔍 Analysis | Inspect single questions (read-only) |
                | ✏️ Workbench | Clusters, items, normalization, numeric values |
                | 📤 Export | Word, JSON, CSV, PNG, dashboard |
                | 🏷️ Themes | Cross-cutting themes for the dashboard |
                | ⚙️ Setup | Survey selection, segmentation editor |
                
                ### Handler types
                
                | Handler | Description |
                |---|---|
                | `single_choice` | Single choice |
                | `multi_choice` | Multiple choice |
                | `multi_choice_binary` | Multiple choice (yes/no per option) |
                | `matrix_likert` | Matrix with a rating scale |
                | `ranking` | Ranking questions |
                | `freetext` | Free-text answers with clustering |
                | `numeric` | Numeric answers with statistics (mean, median, quartiles, IQR outliers) |
                | `numeric_matrix` | Several numeric fields, also with a constant sum |
                | `date` | Dates |
                | `cooperation_matrix` | Cooperation matrix |
                
                ### Several surveys
                
                The tool manages several installed surveys, each in a
                subdirectory of the `surveys/` folder:
                
                ```
                surveys/
                  <survey_id>/
                    survey.yaml          (required: metadata)
                    questions.yaml       (required: questions and settings)
                    documents.yaml       (optional: publication documents)
                    diagram_labels.yaml  (optional: labels of diagrams in them)
                    snapshots/           (automatic: before every write)
                ```
                
                Switch in `⚙️ Setup → Survey selection` or at
                start-up with the environment variable `SURVEY_ID`.
                
                ### Configuration example
                
                ```yaml
                settings:
                  llm_context: "Short domain background for the LLM"
                  segmentation:
                    dimensions:
                      - id: role
                        label: {en: Role}
                        source_question: role
                        bucketing: {type: identity}
                
                question_groups:
                  age:
                    handler: numeric
                    columns: ["age"]
                    label: {en: "How old are you?"}
                    numeric:
                      unit: "years"
                      valid_range: {min: 16, max: 99}
                      decimals: 0
                ```
                
                ### Safety net
                
                Before every write to `questions.yaml`
                (config assistant, segmentation editor, numeric
                values) a snapshot is saved in
                `<survey_dir>/snapshots/`; the last 10
                are kept.
                
                The full documentation is in the `docs/` folder of the
                repository (start with `docs/index.md`).
                
                """)
        
        # =====================================================================
        # GLOBAL EVENT HANDLERS FOR DROPDOWN UPDATES
        # =====================================================================
        
        # after the import: update the dropdowns
        import_btn.click(
            fn=lambda: None,
            inputs=[],
            outputs=[]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_question_groups()),
            outputs=[question_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_question_groups()),
            outputs=[export_question]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_segmentation_options()),
            outputs=[segment_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_freetext_questions()),
            outputs=[extraction_question]
        )
        
        restore_btn.click(
            fn=lambda: None,
            inputs=[],
            outputs=[]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_question_groups()),
            outputs=[question_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_segmentation_options()),
            outputs=[segment_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_freetext_questions()),
            outputs=[extraction_question]
        )
        
        # Config Reload Button Handler
        def reload_config_file():
            # questions.yaml of the active survey (config/ has none)
            config_path = get_active_config_dir() / "questions.yaml"
            load_config()  # update the state
            with open(config_path, 'r', encoding='utf-8') as f:
                return f.read(), "✅ Configuration reloaded"
        
        reload_config_btn.click(
            fn=reload_config_file,
            outputs=[config_editor, config_status]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_question_groups()),
            outputs=[question_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_freetext_questions()),
            outputs=[extraction_question]
        )
        
        # Config Save Button Handler
        save_config_btn.click(
            fn=save_config,
            inputs=[config_editor],
            outputs=[config_status]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_question_groups()),
            outputs=[question_dropdown]
        ).then(
            fn=lambda: gr.Dropdown(choices=get_freetext_questions()),
            outputs=[extraction_question]
        ).then(
            fn=lambda: state.mark_reexport_needed("Configuration (YAML) changed"),
            outputs=[],
        )
        
        # Footer
        gr.Markdown(
            
            "---\n*Survey Analysis Suite*",
            elem_classes="footer"
        )
    
        # A page load shows the preselection of the survey active now, not the
        # one active when the interface was built
        app.load(fn=_language_control_updates, outputs=_language_controls)
        # Choices that depend on the data and configuration of the server: built
        # once at start-up, so a reloaded page would show the state of that moment
        # (e.g. no segmentation offered after an import in another tab)
        app.load(
            fn=lambda: (gr.Dropdown(choices=get_question_groups()),
                        gr.Dropdown(choices=get_question_groups()),
                        gr.Dropdown(choices=get_segmentation_options()),
                        gr.Dropdown(choices=get_freetext_questions())),
            outputs=[question_dropdown, export_question, segment_dropdown, extraction_question],
        )
    
    return app


def main():
    """Starts the application."""
    app = create_interface()
    
    logger.info(f"Starting the server on {SERVER_NAME}:{SERVER_PORT}")
    if ROOT_PATH:
        logger.info(f"Root-Path: {ROOT_PATH}")
    
    app.launch(
        server_name=SERVER_NAME,
        server_port=SERVER_PORT,
        # Never open a public Gradio tunnel: the UI has no login and handles
        # survey responses. Remote access belongs behind a reverse proxy.
        share=False,
        show_error=True,
        root_path=ROOT_PATH,
        theme=gr.themes.Default(
            font=["ui-sans-serif", "sans-serif"],
            font_mono=["ui-monospace", "monospace"]
        ),
        css="""
        .main-header { text-align: center; margin-bottom: 20px; }
        .status-box { padding: 10px; border-radius: 5px; }
        """
    )


if __name__ == "__main__":
    main()
