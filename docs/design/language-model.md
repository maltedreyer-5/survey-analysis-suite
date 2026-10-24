# Language model

## What the model does

- clusters open answers into topics and writes their names, descriptions and examples;
- splits unclear open answers into items;
- writes the summary of each question and the texts of statistical findings and
  associations;
- translates (open answers into the source language; labels, texts and documents into
  the output languages);
- suggests settings in the Config assistant, keywords of themes, and the assignment of
  items after Workbench changes.

## What it never decides

Every number in a report or in the dashboard is computed by the code. Which group
differences and associations are significant is decided by the tests
([Statistics](statistics.md)); the model receives the facts of a finding (groups,
shares, test, corrected p-value, effect size) and puts them into words. A possible
reading of a finding is kept apart from its facts and marked as such. Texts are stored
with a hash of their facts: when the facts change, the text is written again; a text
corrected by hand is never overwritten.

Reason: a language model is good at wording and at grouping texts by meaning, and
unreliable at counting and at statistics. The split keeps every statement checkable
against the data.

## Checks around the clustering

The answer of the model is not taken as it is:

- every answer must end up in exactly one cluster; missing ones are distributed in
  proportion to the cluster sizes, duplicates are removed;
- a cluster with too large a share of all answers is split again;
- clusters with nearly the same name are merged; clusters with a single answer join
  the largest cluster;
- a run with too few clusters is repeated with an explicit request to tell the answers
  apart.

## Prompts

The prompts are in `backend/prompts/<source language>.yaml` (German and English), not
in the code, so that they can be read and adjusted in one place. Both catalogs have the
same keys and placeholders. Every prompt names the language the answer must be in.

## Request parameters

A temperature is sent only if `LLM_TEMPERATURE` is set. Current reasoning models
reject any temperature or accept only their default, so sending none is the setting
that works with every model. `LLM_TASK_TEMPERATURES=true` restores task-specific values
(low for descriptions, higher for retries) for models that accept them.
`LLM_EXTRA_PARAMS` passes model-specific parameters without changes to the code.

## Limits

- Results depend on the model and can differ between runs; the suite does not make
  them reproducible. Check clusters and texts before publishing.
- The quality of the clusters and texts depends on the model; small local models give
  vaguer names and more repetition.
- The checks above repair the structure of a clustering, not its meaning: a cluster
  can be well-formed and still mix two topics. The Workbench is the place to fix that.
