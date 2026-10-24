# Changelog

## 1.0.0 (first public release)

- Analysis of questionnaire surveys by question type: single and multiple choice,
  yes/no matrices, rating matrices, cooperation matrices, rankings, open answers,
  numbers, number matrices with constant sums, dates; scale metrics for ordered
  scales and the Net Promoter Score.
- Import of CSV, Excel and SPSS data; LimeSurvey (`.lss`) and Qualtrics (`.qsf`)
  structure files complete the configuration.
- Segmentation by any number of dimensions (identity, value mapping, numeric buckets),
  cross filters, minimum group sizes.
- Tests of group differences and associations with effect sizes and
  Benjamini-Hochberg correction; reported findings with their facts.
- Open answers: item extraction, clustering with a language model, segment-specific
  clusters, manual corrections in the Workbench, re-export.
- Summaries per question with configurable sections; texts of findings and
  associations written from computed facts.
- Named scales with polarity, defined once and reused by questions and dimensions.
- Output in any number of languages; translation of open answers into the source
  language and of all labels into the output languages, each list with its context;
  translation memory.
- Word reports, JSON and CSV exports, publication documents from Markdown sources.
- Static, self-contained dashboard with filters, cross filters, themes, group
  comparisons and associations, in German, English, French and Italian.
- Web interface (Gradio) with configuration editor, Config assistant, segmentation
  editor, snapshots of every configuration change.
- Two invented example surveys (German and English).
