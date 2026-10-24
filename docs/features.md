# Features

The suite turns the raw answers of a questionnaire survey into reports and a
dashboard in several languages. It is built for surveys of a few hundred to a few
thousand respondents, with closed and open questions, whose results are compared
between groups of respondents (for example role, faculty, age group, site).

## Workflow

1. **Import** the answers (CSV, Excel `.xlsx`/`.xls`, SPSS `.sav`) and optionally the
   questionnaire structure (LimeSurvey `.lss`, Qualtrics `.qsf`). The structure file
   completes the configuration with the full question texts, help texts and answer
   options in all languages it contains; LimeSurvey answer codes are replaced by their
   texts.
2. **Configure** the survey in `questions.yaml` (see [Configuration](configuration.md)),
   in an editor in the **Configuration** tab, or with the **Config assistant**, which
   suggests settings with the language model.
3. **Run the pipeline**: translation of open answers and labels, item extraction,
   analysis of every question, themes, associations, group comparison, summaries,
   Word reports and the dashboard export, in one run.
4. **Correct** the clusters of open answers in the **Workbench** and export again
   (re-export) without repeating the whole pipeline.
5. **Publish**: the Word reports, the dashboard folder, and optional publication
   documents (cover, introduction, method note) built from Markdown sources.

## Question types

| Type (`handler`) | Typical question | Results |
|---|---|---|
| `single_choice` | one answer from a list | counts and shares; for ordered scales mean, median, top and bottom box; Net Promoter Score for 0–10 questions |
| `multi_choice` | several answers from a list; one column per option, a filled cell counts as chosen | counts and shares of respondents per option |
| `multi_choice_binary` | several answers; one column per option with yes/no values (configurable) | counts and shares per option |
| `matrix_likert` | several items rated on the same scale | distribution per item, scale metrics per item |
| `cooperation_matrix` | importance of several partners on a scale | distribution and an importance score per partner |
| `ranking` | order items by priority | weighted points per item (rank 1 gets the most) |
| `freetext` | open answer, one or several fields | topic clusters with counts, descriptions and examples |
| `numeric` | a number | mean, median, standard deviation, quartiles, histogram, outliers (IQR rule) |
| `numeric_matrix` | several numbers, for example a budget split | statistics per field, check of a constant sum |
| `date` | a date | distribution over periods |

Ordered answer scales can be defined once and reused by several questions
(**named scales**); they also carry the polarity (positive, neutral, negative) that
colours the charts.

## Groups of respondents (segmentation)

Every configured dimension groups the respondents, from the answer of one question or
from a data column:

- **identity**: every answer is a group (for example the role);
- **value mapping**: several answers form one group (for example faculties to areas);
- **numeric buckets**: numbers to classes (for example age groups);
- answers not covered by the mapping are kept, dropped, or collected in a group of
  their own.

All results are available per group. **Cross filters** combine two or more
dimensions (for example role × site). Groups smaller than the configured minimum
(default 5 respondents) are not shown, to protect the respondents.

## Statistics

- **Group differences**: every dimension is tested against every analysed variable of
  a question (chi-square or Fisher's exact test, Kruskal-Wallis for numbers); see
  [Methods](methods.md#4-statistical-tests). All tests
  of one question form one family; the false discovery rate is controlled with the
  Benjamini-Hochberg procedure. A difference is reported as a finding only if it is
  significant, has at least a weak effect, and the shares of the highest and the lowest
  group differ by at least 15 percentage points; at most three findings per question
  and dimension.
- **Associations** between questions (for example satisfaction and use of a tool):
  Kendall's tau-b, Kruskal-Wallis or chi-square depending on the measurement level,
  with effect sizes and Benjamini-Hochberg correction; at least 20 respondents per pair
  and 5 per group.
- Questions answered with "don't know" and similar answers: such answers are
  recognised from a word list (German, English, French, Italian; overridable per
  question) and left out of scale metrics and tests.

See [Statistics](design/statistics.md) for the reasons behind these choices.

## Open answers

- **Item extraction** splits answers that name several things into single items (by
  rules first, with the language model for unclear cases), so that "costs and data
  protection" counts for two topics.
- **Clustering**: the language model groups the answers or items into topics with a
  name, a description and typical examples. Clusters that are too dominant are split;
  duplicates are merged; every answer is assigned.
- **Segment-specific clusters**: for dimensions where it is enabled, the answers of
  each group are clustered on their own, to show topics that only one group raises.
- **Workbench**: rename clusters and edit their descriptions per language; add a
  cluster; delete a cluster and move its items to another one; move single items to
  another cluster and correct their text in the item table; distribute all items again
  over a given list of cluster names (*Recluster with these names*, which also merges or
  splits clusters); unify spelling variants; clean up numeric answers. Every change is
  stored and goes into the next re-export; an item moved by hand keeps its cluster.

## Texts written by the language model

- a short summary per question (description, interpretation, group differences;
  the sections are configurable per survey, question type or question);
- descriptions of the statistical findings and of the associations;
- names and descriptions of clusters, keywords of themes.

The model writes about results that the suite has counted and tested; it does not
decide which differences are significant. See [Language model](design/language-model.md).

## Themes

Themes group questions across the questionnaire (for example "data protection").
They are defined in the configuration or found by the language model from keywords;
the dashboard offers a view per theme, and associations between themes.

## Exports

| Output | Contents |
|---|---|
| Word report per output language | every question with chart, table and summary; group comparisons; an overview of the questionnaire as appendix |
| Dashboard data | `dashboard_data.json` and the other files of the [dashboard data format](dashboard-data.md) |
| JSON, CSV | results per question; CSV cells that would start a spreadsheet formula are neutralised |
| Publication documents | Word documents built from Markdown sources of the survey, translated paragraph by paragraph |

## Dashboard

A static web page (`dashboard/`) that shows the results with filters for the groups
of respondents, cross filters, a view per theme, group comparisons with the
segment-specific clusters, deviations from the average, and the associations. It needs
no server: it can be opened from the file system or put on any web server. All
libraries and the font are included; the page makes no external requests.

## Languages

- **Source language** (language of the answers and of the configuration): German or
  English.
- **Output languages**: any; without a setting, the source language only. Reports, the
  dashboard and all labels are produced in every output language. Fixed texts of the
  interface and the reports exist in German, English, French and Italian; other
  languages fall back to English for those.
- Open answers in another language are translated into the source language before they
  are analysed. Question texts, answer options, scales and group names are translated
  into the output languages, each list as a whole with its context; labels written in
  the configuration are never overwritten.
- The web interface itself is in English.

## Limits

- **No login.** The web interface has no users, roles or access control (see
  [System impact](system-impact.md)).
- **Source languages** are German and English only; the prompts of the language model
  exist for these two.
- **Language detection** of open answers recognises German, French and English by
  heuristics; answers in other languages count as the source language.
- **Dual-scale matrices** (LimeSurvey type `1`) are not analysed as two scales; a text
  matrix becomes one free-text question per cell.
- **Clusters and summaries depend on the language model**: they can differ between
  runs and between models; check them in the Workbench before publishing. Without a
  model, open answers are not clustered and findings carry only their facts.
- **Segment-specific clusters** are formed per group and are not tested against each
  other; only the overall clusters are tested between groups.
- **Many tests**: one test per option or item and dimension; the correction keeps the
  false discovery rate but costs power, so small real differences can go unreported.
- **Ranking**: an item a respondent did not rank receives no points from that
  respondent; the score mixes how often an item was ranked and how high (the mean rank
  among those who ranked it is reported as well).
- **One survey at a time**: several surveys can be installed side by side, but the
  interface works on the active one.
