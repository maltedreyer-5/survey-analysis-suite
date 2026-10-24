# User guide

Reference of the web interface, tab by tab. The order of the tabs follows the usual
course of an analysis; the [Tutorial](tutorial.md) goes through it once with an example.
Labels of the interface are given in *italics*.

All tabs work on the **active survey** (see Setup). Every action that writes
`questions.yaml` keeps a snapshot of the previous version first.

## Overview

The state of the active survey: data imported, configuration checked, analyses run,
exports written, and the next step that is missing. *Refresh status* reads the state
again.

## Config assistant

Suggestions of the language model for settings that are still missing.

| Element | Meaning |
|---|---|
| *Area* | `LLM domain context`, `Segmentation dimensions`, `Numeric settings`, `Schema overrides`, or `Everything` |
| *Scope* | `Unconfigured settings only` keeps what is set; `Overwrite existing settings too` replaces it |
| *Create suggestions* | sends the question texts (and sample answers for numeric settings) to the language model |
| *Suggestions* | one row per suggestion: target, proposed value, reason, confidence |
| *Apply all*, *Discard suggestions* | write all suggestions into `questions.yaml`, or drop them |
| *Snapshots* | *Refresh snapshots* lists the saved versions; *Restore the last snapshot* restores the newest (the current file becomes a snapshot itself) |

Schema overrides with a confidence below 0.6 are not suggested.

## Import

| Element | Meaning |
|---|---|
| *Upload a CSV/Excel/SPSS file* | the answers (`.csv`, `.xlsx`, `.xls`, `.sav`) |
| *Structure (LimeSurvey .lss, Qualtrics .qsf)* | optional; completes or creates the configuration |
| *Import* | stores the answers as a new import of the active survey |
| *Restore the last session* | loads the newest import of the active survey and its stored analyses |
| *Data preview*, *Database status* | the first 20 rows; imports, analyses and translations in the database |
| *Translate answers* | translates open answers in other languages into the source language (also done by the pipeline) |
| *Review and edit translations* | *Load translations* lists the stored translations into a *Target language*; corrected cells are kept with *Save changes*; *Delete all translations* needs a confirmation and affects all surveys |

The *Import status* reports the rows, the recoded answer codes, the configuration
created from a structure file or from the data, columns without a question, and
configured columns missing in the data. The file fields are emptied when the active
survey changes.

## Pipeline

| Section | Elements |
|---|---|
| *Complete pipeline* | steps (*Item extraction*, *Theme analysis*, *Deep theme analysis*, *Segment comparison*, *LLM summaries*), *Export languages*, *Start the complete pipeline* |
| *Regenerate summaries* | writes the summaries again (for example after a change of the summary sections); *Update the Word reports* writes the reports afterwards |
| *Re-export after Workbench changes* | *Reassign items (LLM)*; *Start the re-export* |

The *Pipeline status* lists every step with its result, the requests to the language
model per task, and errors. The *Export directory* names the folder of the run
(`full_pipeline_<date>_<time>` or `reexport_<date>_<time>` under the output directory).

## Configuration

The editor shows `questions.yaml` of the active survey. *Save* checks the YAML, keeps a
snapshot and writes the text as typed (with its comments); *Reload* reads the file again.
The *Status* lists errors and warnings of the check ([Configuration](configuration.md)).

## Item extraction

Splits open answers into items before clustering (section 2.10 of [Methods](methods.md)).
*Extract all free-text questions* runs it for every open question; per question,
*Preview* shows the split of some answers and *Extract* runs it; *Extraction status*
lists the questions with their number of items. The pipeline runs this step itself.

## Analysis

The results of one question, overall or for one group.

| Element | Meaning |
|---|---|
| *Segmentation*, *Segment value* | a dimension and one of its groups (empty: all respondents) |
| *Question group*, *Language* | the question and the language of the labels |
| *Show* | table, chart, statistics; for open answers the clusters (*Cluster details*) |
| *Analyze all*, *Recalculate all* | analyses every question again (also done by the pipeline) |

## Workbench

Manual corrections. Every saved change marks the exports as outdated; *Re-export now*
in the banner or the Pipeline tab writes them again.

| Tab | Elements |
|---|---|
| *Clusters* | *Free-text question*; the table with a name per output language, the description and the number of items (double-click a cell to edit, *Save changes*); *Add a cluster*; *Recluster with these names* (distributes all items over the names in the table, which also merges or splits clusters); *Delete a cluster* with *Move its items to* |
| *Items* | *Question*, *Cluster filter*, *Search*; the table *ID*, *Original*, *Extracted*, *Cluster*: correct the text in *Extracted*, type another cluster's name in *Cluster* to move the item (empty: no cluster), *Save changes* |
| *Normalization* | *Find similar items* groups spelling variants above a minimum similarity; per group the *Suggestion* and *Apply*; *Apply normalization* |
| *Numeric* | per numeric question the invalid values and those outside the range; *Save range (with snapshot)* sets `valid_range` |

An item moved by hand keeps its cluster: a re-export with *Reassign items (LLM)* does
not move it again. *Prepare everything* runs the analysis and the item extraction for the
open questions when they have not run yet.

## Export

| Section | Elements |
|---|---|
| single export | *Question group*, *Export formats* (Word, JSON, CSV, PNG), *Languages*, *Export* |
| *Complete export* | *Report title*, *Language*, *Export formats*, *Recalculate all analyses*, *Start the complete export (with segments)* |
| dashboard | *Include analyses by segment*, *Languages*, *Export dashboard data* |
| publication documents | *Languages*, *Create all documents*, *Update missing/changed only*; *Cache management*: *Document*, *Language*, *Clear the translation cache* |

*Export status* and *Export directory* name what was written.

## Themes

| Element | Meaning |
|---|---|
| *Add a theme* | *ID* and *Label (source language)*; the label is translated into the other output languages |
| keywords of a theme | *Save keywords*; *Extract keywords with the LLM* proposes them |
| *Quick analysis (keyword matching)* | assigns questions to themes by their keywords |
| *Deep analysis (cluster check)* | checks the clusters of open answers with the language model |
| *Export themes as JSON* | writes the themes for the dashboard |

## Setup

**Survey selection.** *Choose a survey* and *Switch to the chosen survey*; *Refresh list*
finds new survey directories. *Create a new survey* asks for *Id*, *Title*, the
*Language of the answers (source language)* and *Further output languages*, writes the
survey directory and switches to it. Switching resets the data in memory; the imports in
the database are kept (*Restore the last session* in the Import tab).

**Segmentation dimensions.** The list of dimensions; *New dimension* or choose one to
edit: *Id*, *Label*, source question or column, *bucketing* (identity, value mapping
with its table, numeric buckets with their table), *Free text: segment-specific clusters*,
*Advanced: handling of unmapped values*. *Calculate distribution* shows the groups the
dimension would form with the imported data; *Save (with snapshot)*, *Delete this
dimension*. Below: cross filters and the minimum sizes.

## Info

The layout of the survey directories, the settings of the suite and where the
documentation is.

## Limits

- One survey is active at a time, for all users of the running interface.
- Long steps (pipeline, re-export, complete export) cannot be stopped from the interface;
  the status shows their progress.
- The interface is in English.
