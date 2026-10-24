# Dashboard data format

The dashboard reads the JSON files that the dashboard export writes. They are produced
by `backend/export/dashboard.py` and its helpers; the dashboard reads only this format
(there are no fallbacks to other formats). All texts are given per output language,
`{lang: text}`; keys of answers are stable identifiers derived from the label in the
source language.

| File | Required | Contents |
|---|---|---|
| `dashboard_data.json` | yes | metadata, groups of respondents, all questions with their results |
| `themes.json` | yes | themes and their questions |
| `survey_structure.json` | no | the report groups and the order of the questions |
| `correlations.json` | no | associations between themes and questions |
| `segment_clusters.json` | no | segment-specific clusters of open answers |
| `segment_insights.json` | no | tested group differences per question |

## `dashboard_data.json`

```json
{
  "metadata": {...},
  "segments": {...},
  "questions": {...}
}
```

### `metadata`

| Key | Meaning |
|---|---|
| `survey_title`, `about` | per language |
| `survey_year`, `export_date`, `version` | |
| `n`, `total_responses` | number of respondents |
| `source_language`, `languages` | source and output languages |
| `min_n`, `warning_n` | groups below `min_n` are not exported; below `warning_n` they are marked |
| `cross_filter_support`, `cross_filters`, `cross_filter_min_n` | configured combinations of dimensions, e.g. `[["role", "site"]]` |

### `segments`

One entry per dimension, in the configured order:

```json
"role": {
  "label": {"en": "Your role at the university", "de": "..."},
  "source_question": "role",
  "values": [{"key": "study", "label": {"en": "Study", "de": "Studium"}}]
}
```

`values` are in the configured order of the dimension; the dashboard shows them in
this order.

### `questions`

One entry per question:

| Key | Meaning |
|---|---|
| `id`, `type` | id and question type |
| `text`, `full_text`, `help` | short title, full text and explanation, per language |
| `options` | answer options `[{key, label, polarity?}]` (choice and ranking questions) |
| `ordinal` | `true` for ordered scales |
| `scale`, `rows`, `items` | matrix questions: scale points `[{key, label, polarity?}]`, rows and their labels |
| `clusters` | open answers: `[{id, label, description, examples, results}]` |
| `results` | the results: `all` (all respondents), `by_<dimension>` per group, `by_<dim1>_<dim2>` per cross filter |

`polarity` (`positive`, `neutral`, `negative`) comes from a named scale and decides
the colours of the charts.

Results by question type (`results.all`; the group results have the same form, one
object per group key):

| Type | Fields |
|---|---|
| `single_choice`, `multi_choice`, `multi_choice_binary`, `ranking` | `n`, `values` (count per option key), `percentages`; for ordered scales `scale_metrics`, for 0–10 questions `nps` |
| `matrix_likert`, `cooperation_matrix` | `n`, `by_row` (per row: `n`, `values`, `percentages`, `distribution`, `scale_metrics`), `items` |
| `freetext` | `n` (respondents), `n_items`, `n_responses`, `avg_items_per_response`, `cluster_counts`, `cluster_percentages`, `analysis_mode` (`items` or `raw`) |
| `numeric` | `n`, `statistics`, `histogram_bins`, `values`, `percentages`, `unit`, `decimals` |
| `numeric_matrix` | `n`, `fields` (per field: `n`, `mean`, `median`, `std`, `min`, `max`, `q25`, `q75`), `constant_sum` (`total`, `n_respondents`, `n_matching`, `share_matching`, `mean_share` per field) |
| `date` | `n`, `values` and `percentages` per period (e.g. `2026-03`), `date`: `granularity` (day, month or year), `first`, `last`, `median`, `n_invalid` |

#### `scale_metrics`

`n` (answers on the scale), `n_excluded` (answers outside it, such as "don't know"),
`mean`, `median`, `top_box`, `bottom_box`, `box` (size of the boxes),
`min_value`, `max_value`, `top_labels`, `bottom_labels`.

#### `nps`

`n`, `n_excluded`, `n_promoters`, `n_passives`, `n_detractors`, `promoters`,
`passives`, `detractors` (shares in percent), `nps` (−100 to 100).

#### numeric

| Field | Meaning |
|---|---|
| `statistics` | `n`, `n_invalid` (not a number or outside `valid_range`), `mean`, `median`, `std`, `min`, `max`, `q25`, `q75`, `outliers` |
| `histogram_bins` | `[{label, min, max, count}]` |
| `values`, `percentages` | count and share per bin label (the same keys as `histogram_bins`) |

Outliers follow the IQR rule: values below `q25 − 1.5·IQR` or above `q75 + 1.5·IQR`
(IQR = `q75 − q25`), computed only with at least four values.

#### Clusters

```json
{"id": 3, "label": {"en": "Costs"}, "description": {"en": "..."},
 "examples": {"en": ["..."]},
 "results": {"all": {"count": 12, "percentage": 21.4, "n": 56}, "by_role": {...}}}
```

`count` is the number of respondents who mentioned the topic; `percentage` is the
share of the respondents of the question.

## `themes.json`

`metadata` and `themes`: per theme `label`, `description` (per language), `icon`,
`questions` (ids), `order`.

## `survey_structure.json`

`metadata` and `groups`: the report groups in their order, each with `label` and the
question ids.

## `correlations.json`

`method` (tests, correction, thresholds), `summary`, `theme_correlations`,
`cross_theme_correlations`: per pair of questions the test, the effect size and its
strength, the corrected p-value, the cells that carry the pattern, and a text per
language.

## `segment_clusters.json`

`parameters`, `dimensions` (per dimension the questions with `clusters_by_value`:
the clusters formed for each group on its own), `summary`.

## `segment_insights.json`

`parameters`, `method`, `questions`: per question and dimension the tested group
differences that are reported as findings, with their facts and a text per language.

## Limits

- The format has no version negotiation: dashboard and exporter must come from the
  same version of the suite.
- Groups below `min_n` are missing from the files, not hidden by the dashboard; data
  of small groups never leaves the backend.
- The texts in the files were written by the language model at the time of the export;
  the dashboard does not change them.
