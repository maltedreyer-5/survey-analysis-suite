# Customising and extending

How to adapt texts and prompts, add a language, and add a question type. Every change is
checked with `bash scripts/check.sh` ([CONTRIBUTING](../CONTRIBUTING.md)).

## Texts and prompts

- **Fixed texts of reports and exports**: `backend/locales/<lang>.yaml`, one file per
  output language. A new key goes into every file.
- **Texts of the dashboard**: `dashboard/js/i18n.js`, one block per language.
- **Prompts of the language model**: `backend/prompts/de.yaml` and `en.yaml`, one catalog
  per source language, with the same keys and placeholders in both. Every prompt names
  the language the answer must be in.
- **Sections of the summaries**: per survey, question type or question in the
  configuration ([Configuration](configuration.md#summaries-sections)), without code.

## A new output language

1. Copy `backend/locales/en.yaml` to `backend/locales/<lang>.yaml` and translate the
   values; the suite finds the file by its name.
2. Add a block for the language to `dashboard/js/i18n.js` with every key of the English
   block; a key missing in the block is shown in English.
3. Add the language to `settings.survey_metadata.languages` of a survey.

Texts of the survey (questions, options, groups) are translated by the language model and
need no change.

## A new source language

A source language needs a prompt catalog: copy `backend/prompts/en.yaml` to
`backend/prompts/<lang>.yaml`, translate the instructions (keep keys and placeholders), and
add the code to `SUPPORTED_SOURCE_LANGUAGES` in `backend/utils/languages.py`. Evasive
answers are recognised from the texts of the catalogs in `backend/locales/`, the yes/no
values from the list in `backend/utils/binary_values.py`; add the words of the new
language there.

## A new question type

1. A handler in `backend/handlers/`, derived from `BaseHandler`: `analyze()` returns an
   `AnalysisResult` (n, data, percentages, metadata), `create_chart()` the chart, and
   `default_summary_schema()` the sections of its summary.
2. Register it in `HANDLER_REGISTRY` (`backend/handlers/__init__.py`) under the name used
   as `handler` in the configuration.
3. Analysis variables for the statistical tests in `backend/utils/analysis_variables.py`,
   with their measurement level ([Methods, section 4.1](methods.md#41-analysis-variables)).
4. The export of its results in the dashboard data (`backend/export/result_format.py`) and
   a chart in `dashboard/js/charts.js`; exporter and dashboard change together
   ([Dashboard data format](dashboard-data.md)).
5. Where structure files have a matching type, the mapping in the adapters
   (`backend/utils/structure_lss.py`, `structure_qualtrics.py`, `structure_spss.py`).
6. Tests for the analysis, the export and the chart; the rules of
   [Architecture](architecture.md#invariants) apply.

## Limits

- A source language that is not in `SUPPORTED_SOURCE_LANGUAGES` is refused by the
  configuration check.
- The dashboard has no plug-in mechanism; a new question type changes `charts.js` and
  `app.js`.
