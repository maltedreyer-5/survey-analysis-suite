# Data protection in practice

Survey answers can contain personal data, above all in open answers. This chapter lists
what the suite processes where, so that the person responsible can assess it under the
applicable rules. It is no legal advice.

## What is sent to the language model

| Step | Sent to `LLM_BASE_URL` |
|---|---|
| translation of open answers | the open answers in another language than the source language |
| item extraction | open answers that the rules cannot split |
| clustering | the open answers or items of each open question |
| summaries, findings, associations | question texts, answer options, counted results and test results; cluster names and examples |
| translation of labels and documents | question texts, answer options, group names, Markdown sources of publication documents |
| Config assistant | question texts and, for numeric settings, a few example answers |

Not sent: the data rows as a whole, the answers of closed questions per respondent, and
columns that no question uses (ids, e-mail addresses, time stamps).

## Before the import

- Remove columns that identify respondents (names, e-mail addresses, tokens, IP
  addresses) from the data file, or keep them out of the configuration; the suite does
  not need them.
- Open answers can name persons, units or places. Where this matters, pseudonymise them
  before the import, or use a language model in your own network.
- Set `minimum_threshold` so that no group is small enough to identify a person
  (default 5).

## In the exports

- Results are aggregated; groups below `minimum_threshold` are not exported.
- **Cluster examples are verbatim open answers.** They appear in the Word reports and the
  dashboard. Read them before publishing; rename or correct clusters in the Workbench.
- Summaries and texts of findings are written from aggregated results.

## Where data is kept, and how to delete it

The database in `SURVEY_DATA_DIR` holds the imported answers, items, clusters,
translations and texts; the exports are in `SURVEY_OUTPUT_DIR`; uploaded files are copied
to the temporary directory of Gradio. [System impact](system-impact.md) lists every place
and how to remove it. Deleting the database removes all imported answers of all surveys.

## Limits

- The suite cannot recognise personal data in open answers; it does not anonymise.
- What the provider of the language model does with requests is outside the suite.
- The web interface has no access control; anyone who can reach it sees all data
  ([System impact](system-impact.md)).
