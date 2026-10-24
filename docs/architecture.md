# Architecture

## Components

```
survey-analysis-suite/
├── backend/                 Python application
│   ├── app.py               web interface (Gradio): tabs and their events, no logic
│   ├── core.py              application state, loading of the active survey
│   ├── services/            what the tabs do (import, analysis, pipeline, Workbench, setup)
│   ├── handlers/            one analysis per question type
│   ├── export/              pipeline, dashboard export, segment results, reports
│   ├── utils/               statistics, segmentation, language model, translation,
│   │                        charts, Word documents, database, survey structure files
│   ├── themes/              theme analysis
│   ├── prompts/<lang>.yaml  prompts of the language model, one catalog per source language
│   ├── locales/<lang>.yaml  fixed texts of reports and exports, one catalog per output language
│   ├── config/glossary.yaml base glossary of the translations
│   └── surveys/<id>/        one directory per survey (configuration, snapshots, documents)
├── dashboard/               static web page: index.html, js/, vendor/, data/
├── examples/                generator of the example surveys and their data
└── docs/                    this documentation
```

`dev-checks/` (if present in a working copy) holds the checks that run before a
release; it is not part of the published repository.

## Data flow

```
data file ─┐                                   ┌─> Word reports (per language)
structure ─┴─> import ─> database ─> analysis ─┼─> dashboard data (JSON) ─> dashboard/
               (raw_responses)   (handlers,    ├─> JSON / CSV
                                  segments,    └─> publication documents
                                  statistics,
                                  language model)
```

1. **Import** stores the answers as rows (`raw_responses`, one JSON object per row,
   with the row index of the data file) and completes the configuration from a
   structure file.
2. **Loading the configuration** expands named scales and fills missing output
   languages of the labels from the translation memory; the file stays as written.
3. **Segmentation**: the `SegmentResolver` adds one column per dimension
   (`__segment_<id>__`) to the data; every group result reads those columns.
4. **Analysis**: every question is analysed by its handler, overall and per group;
   results are stored (`analysis_results`). Open answers are split into items
   (`response_items`) and clustered (`freetext_clusters`).
5. **Statistics**: tests of group differences and associations work on analysis
   variables derived from the answers (not on counts of mentions); every family of
   tests is corrected.
6. **Texts**: the language model describes the counted results and the tested
   findings; the texts are stored with a hash of their facts.
7. **Exports** read the stored results. The dashboard export writes the files of the
   [dashboard data format](dashboard-data.md); the dashboard reads only them.

## Storage

One SQLite file, `<SURVEY_DATA_DIR>/survey_data.db`, with a schema version in the
table `meta`. Main tables:

| Table | Contents |
|---|---|
| `projects`, `imports`, `raw_responses` | imports per survey and the answers as rows |
| `analysis_results` | results per question, overall and per group |
| `response_items`, `item_history`, `item_normalizations`, `extraction_jobs` | items of open answers and their edits |
| `freetext_clusters` | clusters with names, descriptions, examples, assignments |
| `question_summaries`, `analysis_texts` | texts of the language model and manual corrections |
| `translations`, `document_translations` | translation memory and translated paragraphs of publication documents |
| `themes`, `theme_mappings` | themes and their questions |

The configuration lives in files (`backend/surveys/<id>/`), not in the database, so
that it can be edited, compared and versioned outside the suite.

## External interfaces

- **Language model**: an OpenAI-compatible `chat/completions` endpoint
  (`utils/llm.py`), with retries; the request parameters are configured in `.env`.
- **Rendering service for diagrams** (optional): `MERMAID_RENDER_URL` renders Mermaid
  diagrams of publication documents into images.
- **Dashboard**: reads only its own folder (`data/*.json`, or the converted
  `js/*-data.js` for `file://`).

## Invariants

These rules come from real defects; breaking them has silent consequences.

1. Every result per group goes through the segment context or the
   `SegmentResolver`; raw columns are never filtered directly.
2. Configuration errors are reported, never passed over silently.
3. Every write of `questions.yaml` keeps a snapshot of the previous version first.
4. There are no database migrations: a database of another schema version is refused
   and never changed.
5. Multilingual values are read only through `localized()`, which falls back to the
   source language and then to English.
6. Groups below the minimum size are not exported.
7. The dashboard never falls back to other numbers under a filter without marking it.
8. All translations go through the `TranslationService`; there is no second way.
9. Prompts live in `prompts/<source language>.yaml`, not in the code; both catalogs
   have the same keys and placeholders.
10. Statistical statements come only from analysis variables and the test modules
    (`utils/analysis_variables`, `utils/association`, `utils/segment_tests`,
    `utils/segment_findings`); tests never run on counts of mentions, and respondents
    who did not answer or gave an evasive answer are not part of the base.
11. P-values of a family of tests are corrected before any statement; "no difference"
    is only said where a test was possible, and what was not testable is reported.
12. The language model only words facts the code has computed; every statement keeps
    its facts. Numbers in exports are computed by the code, never by the model.
13. Texts of findings are stored with the hash of their facts; a manual correction is
    never overwritten.
14. `response_items.row_index` is the data row of the import
    (`raw_responses.row_index`); readers join on `(import_id, row_index)`. Items are
    written only by `services/item_writer`, always over all text fields of the
    question.
15. Categorical answers are compared with the configured texts only through
    `utils/answer_values.answer_text`.
16. The dashboard reads only the format the current exporter writes; a new format
    changes exporter and dashboard together. Every visible text of the dashboard comes
    from `js/i18n.js` or from the data, in every language of `i18n.js`.
17. Code never decides on the text of a message; choices in the interface are compared
    by their values, editable tables are read by column keys.
18. Named scales are expanded where the configuration is loaded; code that reads
    `questions.yaml` itself calls `utils.scales.expand_named_scales`.

## Limits

- **One process, one active survey**: the state of the interface is held in memory of
  one Python process; several users share it. The suite is not built for concurrent
  users.
- **SQLite**: fine for one user and surveys of thousands of respondents; not for
  several processes writing at once.
- **Synchronous language-model calls**: requests are made one after another; a run's
  duration grows with the number of open answers.
- **No migrations**: a new schema version needs a new database; the old one stays
  readable with the version of the suite that wrote it.
