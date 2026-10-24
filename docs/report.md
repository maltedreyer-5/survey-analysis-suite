# The Word report

Structure of the report the pipeline writes per output language
(`report_<lang>_<time>.docx`) and of the complete export. The charts are described in
[Charts](charts.md), the computations in [Methods](methods.md).

## Report of the pipeline

| Part | Contents |
|---|---|
| title page | survey title, date, number of analysed questions |
| table of contents | update it in Word: select all (Ctrl+A), then F9 |
| **Part I: Question analyses** | the questions, grouped by the report groups of the configuration |
| **Part II: Statistical associations** | associations between questions and themes |
| **Part III: Differences between groups** | the findings per question and dimension |
| **Part IV: Topics per group** | only with segment-specific clusters: the topics of the open answers of every group |
| **Survey questions overview** | the questionnaire: every question with its type and answer options |

**A question in Part I** has: the question text and its help text; the chart; the table
of counts and shares (with scale metrics, NPS or statistics where they apply); for open
answers the clusters with their counts and examples; the summary in its sections
(by default *Description of results*, *Interpretation*, *Segment differences*; numeric
questions have their own sections); and, where groups were compared, the results by
group.

**A finding in Part III** names the question, the groups and their shares, the test, the
corrected p-value and the effect size, followed by its text. Questions without a
significant difference, and questions that were not testable, are listed as such.

## Complete export

The complete export (Export tab) writes one report in one language with, for every
question, the chart of all respondents and the charts by group for every dimension
(grouped bars, small multiples; for matrices and numeric questions their own charts), and
appendices with the detailed tables per group and the clusters of the open answers.

## Language

Every report is written in one output language: texts of the configuration, fixed texts
of the report and the texts of the language model. Charts carry the labels of the same
language.

## Limits

- The charts are images; their texts cannot be edited in Word.
- The table of contents is a field that Word fills when it is updated.
- Texts written by the language model are part of the report as they were written;
  corrections are made in the suite (or in the document after the export, where they
  are lost at the next export).
