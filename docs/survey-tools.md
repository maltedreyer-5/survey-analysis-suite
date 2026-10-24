# Survey tools

How the question types of the survey tools become the question types of the suite
([Features](features.md#question-types)). A structure file creates the configuration of a
new survey or completes an existing one; without a structure file the configuration is
inferred from the data ([Tutorial, section 9](tutorial.md#9-the-same-route-for-a-data-file-without-a-structure-file)).
Check every imported configuration in the **Configuration** tab ([Recipes](recipes.md)).

## LimeSurvey (`.lss` and responses)

Export the survey structure (*Display/Export → Survey structure (.lss)*) and the
responses as CSV with *question code* as header and *answer codes*. The import replaces
the codes by the answer texts in the source language; all languages of the survey become
labels of the configuration.

| LimeSurvey type | Code | Question type |
|---|---|---|
| list (radio), list with comment, dropdown, 5-point choice, gender, yes/no, language switch | `L`, `O`, `!`, `5`, `G`, `Y`, `I` | single choice; a list numbered 0–10 with a question about recommending becomes an NPS question, other numbered lists ordered scales |
| multiple choice, multiple choice with comments | `M`, `P` | yes/no multiple choice; the text field of "other" counts as the option "other" when it is filled, and is also clustered as free text |
| array (5 point, 10 point, flexible, increase/same/decrease, yes/no/uncertain, by column) | `A`, `B`, `F`, `E`, `C`, `H` | rating matrix |
| ranking | `R` | ranking |
| numerical input | `N` | numeric |
| multiple numerical input (with `equals_num_value`: constant sum), array of numbers | `K`, `:` | number matrix |
| short, long, huge free text, multiple short text | `S`, `T`, `U`, `Q` | free text |
| array of texts | `;` | one free-text question per cell |
| date/time | `D` | date |
| dual-scale array | `1` | not analysed; reported in the import |
| text display, equation, file upload | `X`, `*`, `|` | no data; left out |

System columns of the response file (`id`, `submitdate`, `lastpage`, `startlanguage`,
`seed` and others) are reported and left out.

## Qualtrics (`.qsf` and responses)

Export the survey (*Tools → Import/Export → Export survey*, `.qsf`) and the responses as
CSV with choice texts or values. The adapter reads the question type, selector and
sub-selector of every question.

| Qualtrics question | Question type |
|---|---|
| multiple choice, single answer (vertical, horizontal, column, dropdown, select box), NPS | single choice; NPS with the score |
| multiple choice, multiple answers | multiple choice |
| text entry (single line, essay, form) | free text; a text entry validated as a date: date |
| matrix (Likert, single answer, dropdown) | rating matrix |
| matrix with multiple answers, matrix of text entries, constant-sum matrix | multiple choice per row, free text per cell, number matrix |
| rank order | ranking |
| slider, constant sum | number matrix (constant sum checked) |
| descriptive text, timing, meta info, captcha | no data; left out |
| pick, group and rank; heat map; hot spot; drill-down; file upload; signature | not analysed; reported in the import |

## SPSS (`.sav`)

Most survey tools export SPSS (for example EvaSys, SoSci Survey, Unipark, SurveyMonkey,
QuestionPro, Qualtrics, LimeSurvey). A `.sav` file is imported as data and structure at
once: variable labels become question texts, value labels answer options. It holds one
language and no explicit question types; they are derived:

1. multiple-response sets of the dichotomy kind: multiple choice;
2. consecutive variables with the same name stem and the same value labels: a multiple
   choice (two labels) or a rating matrix (more labels, in code order);
3. a variable with value labels: single choice, options in code order;
4. numbers without labels: numeric; date formats: date; strings: free text, or single
   choice when they repeat a few values.

## CSV or Excel without a structure file

The configuration is inferred from the column names and the values
([Tutorial, section 9](tutorial.md#9-the-same-route-for-a-data-file-without-a-structure-file)).
Question texts are the column names; the order of scale points is unknown and has to be
set.

## Limits

- No survey tool exports whether a list is a scale; worded scales need `ordinal: true`
  or a named scale after the import.
- LimeSurvey writes the same empty value for an unchecked box and an unanswered
  question: respondents who checked nothing in a multiple choice are not in its base.
- Conditions and filters of a questionnaire (who saw a question) are not imported; the
  base of a question is the respondents who answered it.
- Question types listed as "not analysed" are reported in the import status and left out
  of the configuration.
