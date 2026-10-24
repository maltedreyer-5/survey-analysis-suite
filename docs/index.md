# Survey Analysis Suite documentation

The chapters are grouped by what the reader wants to do. Every chapter ends with a
section on limits: what the suite does not do, cannot do, or where it is deliberately
uncertain.

## Getting started

| Chapter | Contents |
|---|---|
| [Features](features.md) | What the suite does: question types, groups of respondents, statistics, open answers, exports, languages |
| [Installation](installation.md) | Requirements, installation, the `.env` settings, first start |
| [Tutorial](tutorial.md) | One survey from the export of the survey tool to the published results |

## Working with the suite

| Chapter | Contents |
|---|---|
| [User guide](user-guide.md) | The web interface, tab by tab |
| [Configuration](configuration.md) | `survey.yaml` and `questions.yaml`: questions, named scales, segmentation, languages, summaries |
| [Recipes](recipes.md) | Common corrections and settings of `questions.yaml` |
| [Survey tools](survey-tools.md) | LimeSurvey, Qualtrics, SPSS and CSV: how their question types are imported |
| [Operation](operations.md) | Step-by-step instructions for recurring tasks, with the commands to check the result |
| [Troubleshooting](troubleshooting.md) | Symptoms, causes and remedies |

## Understanding the results

| Chapter | Contents |
|---|---|
| [Methods](methods.md) | Bases, formulas, tests, thresholds, and the texts of the language model |
| [Charts](charts.md) | Every chart of the reports and the dashboard: what it shows, how to read it |
| [The Word report](report.md) | Parts and contents of the reports |
| [Dashboard guide](dashboard-guide.md) | For readers of a published dashboard |

## Running it responsibly

| Chapter | Contents |
|---|---|
| [Data protection in practice](data-protection.md) | What goes to the language model, preparing the data, what the exports contain |
| [System impact](system-impact.md) | What the suite writes, which connections it opens, and how to undo each change |

## Development

| Chapter | Contents |
|---|---|
| [Architecture](architecture.md) | Components, data flow, storage, invariants |
| [Dashboard data format](dashboard-data.md) | The JSON files the dashboard reads |
| [Customising and extending](extending.md) | Texts, prompts, languages, new question types |

Design notes give the reasons for decisions that are not obvious from the code:

- [Language model](design/language-model.md): what the model writes, and what it never decides
- [Statistics](design/statistics.md): which tests, which thresholds, and why
- [Languages and translations](design/translations.md): source and output languages, translation memory, context
- [Configuration on load](design/configuration-on-load.md): named scales, snapshots, files as written

[Glossary](glossary.md): terms used in the interface and in this documentation.
