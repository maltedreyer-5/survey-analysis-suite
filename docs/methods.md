# Methods

This chapter states how every result is computed: the base of each figure, the formulas,
the treatment of missing and evasive answers, the statistical tests, and the role of the
language model. It is meant for the people who analyse a survey and for those who review
the analysis. The reasons behind the choices are in [Statistics](design/statistics.md)
and [Language model](design/language-model.md).

## 1. Data preparation

**Rows.** Every row of the imported data file is one respondent. The row number of the
file identifies the respondent throughout the analysis, also in the items of open
answers.

**Empty values.** Empty cells and cells holding only spaces are missing values.

**Evasive answers.** Answers such as "don't know", "no answer" and "not applicable" are
recognised from the labels of the text catalogs in every catalog language, plus the
answers listed in `scale_exclude` (per question) and `settings.analysis.missing_values`.
They are counted and reported, and left out of scale metrics and of statistical tests.

**Numbers.** Numeric answers are read with the same rules wherever they are used
(numeric questions and numeric buckets of a segmentation):

- decimal comma and decimal point are both accepted (`3,14`, `3.14`);
- thousands separators are recognised (`1.234,56`, `1,234.56`);
- a percent sign is removed and the number kept (`15%` is 15);
- leading words such as "ca." or "approx." are removed and logged;
- a value that is not a number, or outside `valid_range`, is counted as invalid
  (`n_invalid`) and left out.

**Answer codes.** When a structure file is imported and the data hold the answer codes of
the survey tool, every column whose values are all codes of the question is recoded into
the answer texts of the source language.

## 2. Results per question type

In all types, `n` is the number of respondents in the base of the question. A
respondent is in the base if at least one column of the question holds a value (for a
yes/no multiple choice: at least one "yes" or "no").

### 2.1 Single choice

- Count and share of every answer option; the share is count / n · 100.
- Options without answers are shown with 0.
- With `ordinal: true` (or a named scale), the scale metrics of section 2.3.
- With `nps: true`, the Net Promoter Score of section 2.4.

### 2.2 Multiple choice

- **One column per option, a filled cell means chosen** (`multi_choice`), and
  **one column per option with yes/no values** (`multi_choice_binary`): count and share
  of respondents who chose each option. Shares are relative to n, the respondents who
  answered the question, so they can add up to more than 100 %.
- The value that means "yes" is `binary_values.positive`; without it (or if it matches
  no value of the column), the values `1`, `true`, `y`, `j` and the words for "yes" in
  German, English, French and Italian are used, regardless of case.
- A column listed in `text_item_columns` counts as chosen when it holds any text (the
  "other" option of LimeSurvey, which has a text field and no checkbox).

### 2.3 Scale metrics (ordered scales and matrix rows)

For an ordered single choice and for every row of a rating matrix:

| Metric | Definition |
|---|---|
| `n` | answers on the scale (evasive answers excluded) |
| `n_excluded` | evasive answers and answers outside the scale |
| `mean`, `median` | of the scale values; by default the points are numbered 1, 2, … in scale order, `scale_values` sets other values |
| `top_box`, `bottom_box` | share of the answers in the highest / lowest `box` points (default 2) |

The direction comes from `scale_order` (or the `direction` of a named scale):
`descending` means the first point is the highest. Without it, the direction is read from
the labels where possible ("strongly agree", "very important", …).

### 2.4 Net Promoter Score

For a 0–10 question: promoters answer 9 or 10, passives 7 or 8, detractors 0 to 6.
NPS = share of promoters − share of detractors, from −100 to 100. Answers that are no
number from 0 to 10 are excluded and counted.

### 2.5 Rating matrix and cooperation matrix

- **Rating matrix** (`matrix_likert`): per row the distribution over the scale points
  and the scale metrics of section 2.3.
- **Cooperation matrix** (`cooperation_matrix`): per partner the distribution and an
  importance score: the mean of the position on the scale, with the first point (lowest)
  as 0 and the last as k − 1 for k points. The partners are ordered by this score.

### 2.6 Ranking

With k rank columns, rank r gives k − r + 1 points (rank 1 gives k points).

| Result | Definition |
|---|---|
| score | sum of the points an item received from all respondents |
| share | score / (n · k) · 100: the score relative to the score of an item every respondent put first |
| mean rank | mean of the ranks among the respondents who ranked the item |
| distribution | how often the item was put on each rank |

An item a respondent did not rank receives no points from that respondent.

### 2.7 Numeric question

| Result | Definition |
|---|---|
| `n`, `n_invalid` | valid values; values that are no number or outside `valid_range` |
| `mean`, `median`, `std`, `min`, `max`, `q25`, `q75` | of the valid values (`std` with n − 1) |
| histogram | `bin_count` classes of equal width (default 10) between the smallest and the largest value |
| outliers | values below q25 − 1.5 · IQR or above q75 + 1.5 · IQR, IQR = q75 − q25; computed with at least 4 values |

`decimals` sets the rounding of the displayed values, not of the computation.

### 2.8 Number matrix and constant sum

Per field the statistics of section 2.7. With `constant_sum` (for example 100): the sum
of the fields per respondent is compared with the total; a sum within 0.5 % of the total
counts as matching. Reported: respondents, matching respondents and their share, and
the mean share of each field in the total. Fewer than 90 % matching respondents give a
warning.

### 2.9 Date

The dates are grouped by day, month or year: `date.granularity`, or by the span between
the first and the last date (up to 31 days: day; up to 731 days: month; longer: year).
Reported: count per period, first, last and median date, invalid values.

### 2.10 Open answers

**Item extraction** (`item_extraction.enabled`). An answer that names several things is
split into items: first by rules (lists, separators, line breaks); where the rules are
not sure, the language model decides. Every item keeps the row of its respondent.

**Clustering.** The language model groups the items (`clustering.mode: items`, default
when items exist) or the whole answers (`mode: raw`) into topics, each with a name, a
description and typical examples:

| Setting | Default | Meaning |
|---|---|---|
| `min_cluster_size` | 3 | a cluster needs at least this many items |
| `max_clusters` | 8 | upper limit of clusters |
| `max_cluster_pct` | 50 % (≥ 100 answers), 55 % (≥ 50), 60 % (≥ 20), 70 % (fewer) | a larger cluster is split again |
| `hint` | none | a sentence on how the answers should be told apart |

The result is checked before it is used: every item ends up in exactly one cluster
(missing ones are distributed in proportion to the cluster sizes, duplicates removed);
clusters with nearly the same name are merged; a cluster of one item joins the largest
cluster; a run with too few clusters is repeated.

**Counts.** The count of a cluster is the number of **respondents** with at least one
item in it; its share is relative to the respondents who answered the question. A
respondent can appear in several clusters, so shares can add up to more than 100 %.

**Segment-specific clusters.** For dimensions with `enable_specific_clusters`, the open
answers of every group with at least `specific_clusters_min_n` respondents (default 15)
are clustered on their own. These clusters describe a group; they are not compared
between groups by a test.

## 3. Groups of respondents

Every dimension assigns each respondent to at most one group (section
[Segmentation](configuration.md#segmentation-settingssegmentation) of the configuration).
Respondents without a value for the dimension belong to no group of it. Groups with fewer
than `minimum_threshold` respondents (default 5) are not exported; groups with fewer than
`warning_threshold` (default 10) are marked as small. A cross filter combines dimensions;
a combination needs at least `cross_filter_min_n` respondents (default 5).

## 4. Statistical tests

### 4.1 Analysis variables

Tests never run on counts of mentions. Each question is represented by analysis
variables with a measurement level:

| Question type | Analysis variable | Level |
|---|---|---|
| single choice | the answer | nominal; ordinal for ordered scales |
| multiple choice | per option: chosen / not chosen | binary |
| rating matrix | per row: the answer | ordinal |
| numeric, number matrix | the value, per field | numeric |
| open answer | per cluster: mentioned / not mentioned | binary |
| segmentation dimension | the group | nominal |

Respondents without an answer to a variable, and evasive answers, are not part of its
base.

### 4.2 Choice of the test

For a pair of variables with at least 20 respondents who answered both:

| Levels of the pair | Test | Effect size |
|---|---|---|
| both ordinal or numeric (also two numeric variables) | Kendall's τ-b | τ-b |
| one numeric, the other nominal or binary | Kruskal-Wallis over the categories | ε² |
| all other pairs (nominal or binary with nominal, binary or ordinal) | χ² test of independence on the contingency table | Cramér's V, bias-corrected |

Rules of the contingency table: categories with fewer than 5 respondents are left out
(and reported); the χ² test has no continuity correction; if more than 20 % of the
expected counts are below 5 or any is below 1, a 2 × 2 table is tested with Fisher's
exact test, a larger table counts as not testable. In the Kruskal-Wallis test, groups
with fewer than 5 values are left out; at least two groups must remain.

A group comparison of an ordered scale is therefore a χ² test on the full table (the
dimension is nominal). The test responds to any difference between the distributions of
the groups; the order of the scale points does not enter it.

### 4.3 Strength of an effect

| Effect size | weak from | medium from | strong from |
|---|---|---|---|
| Cramér's V | 0.1 / √k | 0.3 / √k | 0.5 / √k |
| Kendall's τ-b, as r = sin(π · τ / 2) | 0.1 | 0.3 | 0.5 |
| ε² | 0.01 | 0.06 | 0.14 |

k = min(rows, columns) − 1 of the table. Below "weak", an effect is negligible.

The cells that carry a pattern of a contingency table are those with an adjusted
standardised residual above 1.96 (at most three are reported).

### 4.4 Correction for multiple tests

The p-values of a family of tests are adjusted with the Benjamini-Hochberg procedure;
a result is significant if its adjusted value q is below 0.05.

- **Group differences:** the family is all tests of one question (every dimension ×
  every analysis variable of the question).
- **Associations:** the family is all tested pairs of the analysis.

### 4.5 Group differences and findings

For each question and dimension the result is one of three states: *differs* (at least
one test significant with at least a weak effect), *no difference* (tested, nothing
significant), *not testable* (too few respondents or groups for any test).

A difference is reported as a **finding** if it is significant, has at least a weak
effect, and the shares of the highest and the lowest group differ by at least 15
percentage points. At most three findings per question and dimension are reported, the
largest first; further significant differences are counted in the method record.

### 4.6 Associations between questions

Pairs of questions (within a theme, and between themes) are tested with the tests of
section 4.2; significant pairs with at least the configured strength
(`correlations.min_strength`, default weak) are reported, at most three per pair of
questions, five per theme and ten across themes.

## 5. Texts written by the language model

The language model receives the computed facts and returns text; it computes nothing.

| Text | Facts it receives |
|---|---|
| summary of a question | question text, results, scale metrics or statistics, the findings of section 4.5 |
| text of a finding | groups, shares, test, adjusted p-value, effect size and strength |
| text of an association | the pair, the test, the effect and the pattern cells |
| cluster name, description | the items of the cluster |

Each text is stored with a hash of its facts. When the facts change, the text is written
again; a text corrected by hand is kept. Numbers in reports and exports always come from
the computation.

The summary has three sections by default (description, interpretation, group
differences) and four for numeric questions; the sections can be configured per survey,
question type or question.

## 6. Themes

A theme groups questions. In the quick analysis, a question belongs to a theme when its
texts contain one of the theme's keywords; the deep analysis also checks the clusters of
open answers with the language model. The keywords are given in the configuration or
extracted by the language model.

## 7. Translation

Open answers in a language other than the source language are translated into the source
language before item extraction and clustering; the original answers are kept. Labels of
the configuration are translated into the output languages as described in
[Languages and translations](design/translations.md).

## Limits

- The tests assume independent respondents; repeated measurements of the same person and
  clustered samples are outside their assumptions.
- No weighting: results describe the respondents, not a population.
- Group comparisons of ordered scales use the χ² test and do not use the order of the
  scale; a consistent shift of all groups by small amounts can stay below the threshold.
- Clusters, item splits, texts and theme assignments depend on the language model and
  can differ between runs.
- The language detection of open answers knows German, French and English; answers in
  other languages are treated as the source language.
