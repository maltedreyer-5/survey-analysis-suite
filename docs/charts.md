# Charts

The charts of the Word reports and of the dashboard, with what they show, how to read
them and which settings change them. The images come from the English example survey.

Two Word reports contain charts:

- the **report of the pipeline** (`report_<lang>_<time>.docx`): one chart per closed
  question, followed by the table and the summary;
- the **complete export** (Export tab, *Start the complete export (with segments)*):
  the chart of every question and, for every segmentation dimension, the charts by group.

## Colours

The same rules apply to every chart of a question:

| Answers | Colours |
|---|---|
| ordered scale with polarity (named scale) | positive points green, negative red, neutral grey; the ends of the scale stronger than the middle |
| ordered scale without polarity | by position, from the positive end (green) over grey to the negative end (red), in the direction of the scale |
| evasive answers ("don't know") | light grey |
| 0–10 question with `nps: true` | detractors (0–6) red, passives (7–8) grey, promoters (9–10) green |
| answers exactly "yes" / "no" | green / red |
| other questions | a palette without meaning |
| groups in charts by group | a palette per group |

`option_colors` in the configuration sets the colour of single answers.

## Charts of a question

### Bars (single choice)

![Bars of a single choice](images/chart_single_choice.png)

One bar per answer, with count and share of the respondents of the question (n below
the chart). Unordered answers are sorted by frequency; answers of an ordered scale keep
their order.

### Bars of an ordered scale

![Bars of an ordered scale](images/chart_ordered_scale.png)

The points of the scale in their order, coloured by polarity or position. Evasive
answers are shown in light grey at their place in the list. The scale metrics (mean,
median, top and bottom box) are in the table below the chart and leave the evasive
answers out ([Methods](methods.md#23-scale-metrics-ordered-scales-and-matrix-rows)).

### Net Promoter Score

![Net Promoter Score](images/chart_nps.png)

The answers 0 to 10 in their order, coloured by detractors, passives and promoters. The
score itself is in the table.

### Multiple choice

![Multiple choice](images/chart_multiple_choice.png)

Share of the respondents who chose each option; the shares add up to more than 100 %
because respondents choose several options. The note below the chart names the base.

### Rating matrix (100 % bars)

![Rating matrix](images/chart_matrix.png)

One bar per row of the matrix, divided into the shares of the scale points (100 % per
row), in the colours of the scale; evasive answers at the end in light grey.

### Ranking

![Ranking](images/chart_ranking.png)

The score of each item: rank 1 of k gives k points, rank k one point
([Methods](methods.md#26-ranking)). The note below the chart states the points.

### Histogram (numeric question)

![Histogram](images/chart_histogram.png)

`bin_count` classes of equal width (default 10) between the smallest and the largest
valid value; the statistics are in the table.

### Number matrix

![Number matrix](images/chart_number_matrix.png)

The mean of each field. With a constant sum, the share of respondents whose fields add
up to the total is in the table.

### Dates

![Dates](images/chart_date.png)

Answers per day, month or year (`date.granularity`, otherwise by the span of the dates).

## Charts by group (complete export)

A question is not compared with the groups formed from its own answers (role by role).
All charts by group keep the order and the colours of the chart of the question.

### Grouped bars

![Grouped bars](images/chart_grouped_bars.png)

For every answer one bar per group, as the share of the respondents of the group. Suits
the comparison of one answer across the groups.

### Small multiples

![Small multiples](images/chart_small_multiples.png)

One panel per group with the distribution of the answers. Suits the comparison of the
shape of the distributions; each panel names its group and its n.

### Rating matrix by group

![Rating matrix by group](images/chart_matrix_by_group.png)

One panel per row of the matrix, in it a 100 % bar per group.

![Mean per row and group](images/chart_matrix_means_by_group.png)

The mean on the scale per row and group; computed only when the direction of the scale
is known. `matrix_segment_chart` chooses `both` (default), `small_multiples`, `mean` or
`none`.

### Numeric question by group

![Medians by group](images/chart_medians_by_group.png)

The median of each group, the line marks the middle 50 % (first to third quartile). The
median is shown because the groups are compared with the Kruskal-Wallis test, which
compares distributions.

## Dashboard

The dashboard draws its charts in the browser from the [dashboard data](dashboard-data.md),
with the same colours. Its views are described for readers in the
[Dashboard guide](dashboard-guide.md).

![Start page](images/dashboard_start.png)

![Questions](images/dashboard_questions.png)

![Deviations from the average](images/dashboard_group_comparison.png)

![Tested differences](images/dashboard_group_differences.png)

![Associations](images/dashboard_associations.png)

## Limits

- The charts of the reports are images (PNG); their texts cannot be edited in Word.
- Long question texts are shortened at a word boundary in the titles of charts by group.
- The chart types are fixed per question type; the configuration chooses colours
  (`option_colors`) and, for matrices by group, `matrix_segment_chart`.
