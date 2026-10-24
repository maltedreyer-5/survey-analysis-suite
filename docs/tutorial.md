# Tutorial: analysing a survey

This tutorial follows one survey from the export of the survey tool to the published
results: Word reports in several languages and the dashboard. It uses the example
survey in its LimeSurvey form (`examples/limesurvey/`), so every step can be followed
with the files of the repository. Section 9 describes the same route for a survey that
exists only as a data file.

Every step names what the interface shows afterwards and what to check before going on.
The labels in *italics* are the labels of the interface.

Prerequisites: the suite is installed and started ([Installation](installation.md)), and
`backend/.env` points to a language model. Without a language model, steps 4 and 6
produce no suggestions and no clusters; the other steps work.

## 1. Create the survey

**Setup → Survey selection → Create a new survey**

| Field | Value in this tutorial |
|---|---|
| *Id* | `ai_survey_2026` |
| *Title* | `AI at the university 2026` |
| *Language of the answers (source language)* | English |
| *Further output languages* | German, Italian |

*Create and switch to it* writes `backend/surveys/ai_survey_2026/` with `survey.yaml` and
an empty `questions.yaml`, and makes the survey the active one.

Check: the *Status* field reports the directory and "The active survey is now
`ai_survey_2026`".

## 2. Import the answers and the structure file

**Import**

- *Upload a CSV/Excel/SPSS file*: `examples/limesurvey/ai_in_higher_education_en_responses.csv`
- *Structure (LimeSurvey .lss, Qualtrics .qsf)*: `examples/limesurvey/ai_in_higher_education_en.lss`
- *Import*

The structure file defines the questions: their types, texts in both languages of the
LimeSurvey survey, answer options and scale points. LimeSurvey's answer codes (`A1`,
`A2`, …) in the data are replaced by the answer texts.

Check in the *Import status*:

| Line | Meaning |
|---|---|
| `Rows: 240`, `Import ID: …` | the answers are stored in the database |
| `Answer codes converted to answer texts for 6 questions` | codes were found and replaced |
| `Configuration created from the LSS file: 14 questions` | `questions.yaml` was written (a snapshot of the empty file is kept) |
| `5 columns without a question: id, submitdate, lastpage, startlanguage, seed` | system columns of LimeSurvey; they are not analysed |
| `All 25 configured columns found` | every question has its data |

A line starting with ⚠️ names a question that was not configured and the reason.

## 3. Check the configuration

**Configuration**

The editor shows `questions.yaml`. Errors and warnings of the check appear in the
*Status* field. Points to look at after an import:

- **Ordered scales.** A question whose answers form a scale (very satisfied … very
  dissatisfied) needs `ordinal: true` and `scale_order`, or a named scale, so that scale
  metrics are computed. LimeSurvey does not record whether a list is a scale; numbered
  lists (0–10, 1–5) are recognised, worded scales are not.
- **Evasive answers.** "Don't know" and similar answers are recognised from a word list
  and left out of scale metrics; other wordings need `missing_values`.
- **Groups of respondents.** Segmentation dimensions are added in step 4 or under
  **Setup → Segmentation dimensions**.

See [Configuration](configuration.md) for every key and [Recipes](recipes.md) for the
usual corrections.

## 4. Let the language model suggest settings

**Config assistant**

- *Area*: Everything; *Scope*: Unconfigured settings only
- *Create suggestions*

The *Suggestions* table lists each suggestion with its target, the proposed value, the
reason and a confidence. In the example: a domain context (`settings.llm_context`),
a segmentation dimension from the question `role`, and unit and valid range of the
question `age`.

*Apply all* writes the suggestions into `questions.yaml`, after a snapshot. Check the
result in the **Configuration** tab. Suggestions are proposals: a dimension may need a
better label, a range may be too narrow. *Restore the last snapshot* undoes the step.

## 5. Run the pipeline

**Pipeline → Complete pipeline**

The steps are preselected: item extraction, theme analysis, segment comparison, LLM
summaries; the *Export languages* are the output languages of the survey. *Start the
complete pipeline* runs, in this order: translation of open answers and labels, item
extraction, analysis of every question, themes, associations, group comparison,
summaries, Word reports, dashboard data.

Check in the *Pipeline status*: the last lines report "Pipeline finished successfully",
the number of requests to the language model per task, and no line with "failed". The
*Export directory* names the folder of the run, `backend/outputs/full_pipeline_<date>_<time>/`.

## 6. Review the results in the interface

**Analysis**: choose a question in *Question group* and *Show*: the chart, the table and,
for numbers, the statistics. *Segmentation* and *Segment value* show the results of one
group.

**Workbench → Clusters**: choose an open question in *Free-text question*. The table
lists the clusters with their names per output language, a description and the number of
items. Check that every cluster has one clear topic and a fitting name. To correct:

| Action | How |
|---|---|
| rename a cluster, change its description | double-click the cell, type, *Save changes* |
| add a cluster | *Add a cluster*, then rename it |
| move an item to another cluster | **Workbench → Items**: type the cluster's name in the column *Cluster*, *Save changes* |
| delete a cluster | *Delete a cluster*: the cluster and the cluster that takes its items |
| merge or split clusters | edit the list of names in the table, then *Recluster with these names* |
| unify spelling variants | **Workbench → Normalization** |

Every saved change marks the exports as outdated (a banner *Re-export needed*).

## 7. Export again after corrections

**Pipeline → Re-export after Workbench changes**

*Reassign items (LLM)* lets the language model assign the items of changed clusters
again. *Start the re-export* writes the Word reports and the dashboard data into a new
folder `reexport_<date>_<time>/`: with the corrected clusters, new tests of the group
differences and associations, segment-specific clusters formed again, and the summaries
from the cache.

Check: "Re-export finished successfully"; the new cluster name appears in the report.

## 8. Publish

**Word reports**: `report_<lang>_<time>.docx` in the folder of the run, one per output
language. Open each and check the table of contents (update it in Word with Ctrl+A, F9).

**Dashboard**: see [Operation](operations.md#publish-the-dashboard). The two commands:

```bash
cp -r dashboard /path/to/site
cp backend/outputs/reexport_<time>/*.json /path/to/site/dashboard/data/
```

**Themes** (optional): **Themes → Add a theme** with an id and a label in the source
language (translated into the other output languages); *Quick analysis* assigns the
questions by keywords. Themes appear in the dashboard and in the associations.

**Publication documents** (optional): a survey with a `documents.yaml` gets a cover,
an introduction and a method note, and the Word report with a cover in front, from
**Export → Create all documents** ([Operation](operations.md#build-publication-documents)).

## 9. The same route for a data file without a structure file

A survey that exists only as a CSV, Excel or SPSS file (for example from a tool without
a structure export) follows the same steps. In step 2 the structure file is left out;
the import then infers the questions from the column names and the values:

- columns with a common stem (`q1[SQ001]`, `q1_1`, `q1.2`) become one question;
- yes/no columns or columns with one value each become a multiple choice; numbers a
  numeric question or a number matrix (a constant sum is recognised); every row an order
  of the same items a ranking; long texts a free text; a few values shared by all
  columns a matrix; dates a date question;
- columns named like system columns (id, time stamps, tokens) are left out.

The *Import status* lists what has to be checked by hand, for example "answer options
listed by frequency; set their order (or a named scale) if they form a scale". Labels are
the column names; replace them in the **Configuration** tab with the question texts.

## Limits

- The suite does not know what a survey tool did not export: whether a list is a scale,
  which answer means "none of these", which questions were shown to whom.
- LimeSurvey writes the same empty value for a checkbox that was not checked and a
  question that was not answered: in a multiple choice, respondents who checked nothing
  are not part of the base. Offer an option "none of these" in the questionnaire to keep
  them.
- Suggestions of the Config assistant and the clusters depend on the language model;
  review them before publishing.
