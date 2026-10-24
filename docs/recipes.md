# Configuration recipes

Corrections that are often needed after an import, and settings for common question
designs. Each recipe shows the part of `questions.yaml` that changes; the keys are
described in [Configuration](configuration.md).

## Make a list of answers an ordered scale

A structure file does not record whether a list is a scale. For a satisfaction or
agreement question, define the scale once and refer to it:

```yaml
settings:
  scales:
    agreement:
      context: "agreement, from fully agree to fully disagree"
      direction: descending
      points:
        - {label: {en: "Fully agree"}, polarity: positive}
        - {label: {en: "Rather agree"}, polarity: positive}
        - {label: {en: "Rather disagree"}, polarity: negative}
        - {label: {en: "Fully disagree"}, polarity: negative}
        - {label: {en: "Don't know"}}
question_groups:
  q5:
    handler: single_choice
    columns: [q5]
    scale: agreement
```

The labels of the points must be the answer texts in the data. A question that keeps its
own `options` list becomes ordered with `ordinal: true` and `scale_order: descending`.

## Use the same scale for several matrix rows

A rating matrix refers to a named scale in the same way (`scale: agreement`); every row
is then evaluated on that scale.

## Leave an answer out of the statistics

Answers such as "Don't know" are recognised in German, English, French and Italian.
Other wordings are added per question or for the whole survey:

```yaml
question_groups:
  q5:
    scale_exclude: ["Cannot judge"]
settings:
  analysis:
    missing_values: ["Cannot judge", "Prefer not to say"]
```

## Group faculties into areas

```yaml
settings:
  segmentation:
    dimensions:
      - id: area
        label: {en: "Area"}
        source_question: faculty
        bucketing:
          type: value_mapping
          mapping: {"Computer science": "STEM", "Physics": "STEM", "History": "Humanities"}
          unmapped_strategy: {type: bucket, bucket_label: {en: "Other"}}
```

Answers not listed in `mapping` go to the group "Other"; `passthrough` keeps them as
groups of their own, `drop` leaves them out of the dimension.

## Age groups from a numeric answer

```yaml
      - id: age_group
        label: {en: "Age group"}
        source_question: age
        bucketing:
          type: numeric_buckets
          buckets:
            - {label: {en: "under 25"}, max: 25}
            - {label: {en: "25 to 39"}, min: 25, max: 40}
            - {label: {en: "40 and older"}, min: 40}
```

The intervals include `min` and exclude `max`.

## Show the groups in a fixed order

Without an order, groups appear by frequency. A list of values or a named scale sets the
order:

```yaml
      - id: satisfaction_group
        source_question: satisfaction
        bucketing: {type: identity}
        order: satisfaction
```

## Cluster the open answers of each group separately

```yaml
      - id: role
        source_question: role
        bucketing: {type: identity}
        enable_specific_clusters: true
        specific_clusters_min_n: 20
```

Groups with fewer respondents are not clustered on their own.

## Tell the language model how to separate topics

```yaml
question_groups:
  improve:
    handler: freetext
    columns: [improve]
    clustering:
      hint: "Separate requests for training from requests for licences."
      max_clusters: 10
```

## Check a numeric answer

```yaml
question_groups:
  age:
    handler: numeric
    columns: [age]
    numeric: {unit: "years", valid_range: {min: 16, max: 99}, decimals: 0}
```

Values outside the range are counted as invalid and left out of the statistics. The
Workbench (*Numeric*) lists them per question.

## A text field for "other, please specify"

```yaml
question_groups:
  tools:
    handler: multi_choice_binary
    columns: [tools_1, tools_2, tools_other]
    item_labels: {en: ["Chatbots", "Translation", "Other"]}
    text_item_columns: [tools_other]
  tools_other:
    handler: freetext
    columns: [tools_other]
    other_of: tools
```

`text_item_columns` makes the option "Other" count as chosen when its text field is
filled; the free-text question clusters the texts.

## Fix the translation of terms

```yaml
settings:
  glossary:
    "Hochschule": {en: "university", fr: "université", it: "università"}
  keep_terms: ["Moodle", "ChatGPT"]
```

## Limits

- A recipe changes the configuration, not the data: answers that differ in spelling from
  the configured labels are not matched. Unify them first (Workbench, *Normalization*,
  or in the survey tool).
- After a change of the configuration, run the pipeline again; the exports of earlier
  runs keep the old settings.
