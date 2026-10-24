# Contributing

Contributions are welcome: bug reports, corrections of the documentation, new
question types, new languages. Please open an issue first for larger changes, so that
the approach can be agreed before the work is done.

## Language

- Code, identifiers, comments, docstrings, log messages, commit messages, issues and
  the documentation are in **English**.
- The web interface is in English.
- Texts the suite produces for readers of reports and the dashboard are never written
  in the code: fixed texts are in `backend/locales/<lang>.yaml` (one catalog per output
  language), texts of the dashboard in `dashboard/js/i18n.js` (German, English, French,
  Italian), prompts of the language model in `backend/prompts/<lang>.yaml` (one catalog
  per source language, German and English). A new key goes into every catalog of its
  kind.
- Examples and test data may be in other languages where the test is about that
  language.

## Documentation style

The documentation is written as a reference, in the style of a technical handbook:
definitions, procedures, parameters, results and limits, stated plainly.

- State what a thing is and does. Avoid contrastive constructions ("not X, but Y",
  "not only … but also", "more than just"), rhetorical questions and addressing the
  reader ("let's", "here's how", "whether you …").
- No promotional or filler words ("seamless", "powerful", "robust", "simply", "easily",
  "just").
- No dashes as a stylistic device; use a full stop, a colon or parentheses.
- Every chapter ends with its limits.
- Facts in the documentation (settings, defaults, file names, numbers) are checked against
  the code, not copied from other documents.

## "Checked"

A change is checked when this passes:

```bash
pip install -r backend/requirements.txt pytest
bash scripts/check.sh
```

`scripts/check.sh` runs, and the CI runs the same:

| Step | Command |
|---|---|
| Python syntax | `python -m compileall -q backend examples dashboard/build_offline.py scripts` |
| Import smoke test | `python scripts/import_smoke.py` |
| Data and configuration files, no runtime state in the repository, licence headers | `python scripts/check_files.py` |
| Unit tests | `cd backend && python -m pytest -q tests/` |
| Script suites | `python scripts/run_suites.py` |
| Dashboard tests | `node dashboard/tests/<name>.test.js` for every test |
| Documentation links | `python scripts/check_doc_links.py` |

Changes of the dashboard should also be looked at in a browser in every language of
`i18n.js` (open `dashboard/index.html` with the demo data): the tests check the logic,
not the rendered page.

New source files start with the licence header:

```python
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
```

## Rules that must not be broken

These rules come from real defects; breaking them has silent consequences (wrong
numbers, lost work, texts in the wrong language), so a change that breaks one is not
accepted even if all tests pass. Details in [docs/architecture.md](docs/architecture.md#invariants).

1. Results per group go through the segment context or the `SegmentResolver`; never
   filter raw columns directly.
2. Configuration errors are reported, never passed over silently.
3. Every write of `questions.yaml` keeps a snapshot first; editors start from the
   stored configuration, not from form fields.
4. No database migrations: a database of another schema version is refused and never
   changed. A change of the schema raises the schema version.
5. Multilingual values are read with `localized()`; all translations go through the
   `TranslationService`; prompts are read with `utils.prompts.prompt()`, and both
   prompt catalogs keep the same keys and placeholders.
6. Statistical statements come only from `utils/analysis_variables`,
   `utils/association`, `utils/segment_tests` and `utils/segment_findings`; tests never
   run on counts of mentions; p-values of a family are corrected first.
7. The language model only words facts computed by the code; numbers in exports are
   never written by the model; a text corrected by hand is never overwritten.
8. `response_items.row_index` is the data row of the import; items are written only by
   `services/item_writer`, over all text fields of the question.
9. The dashboard reads only the format the current exporter writes; exporter and
   dashboard change together. Every visible text of the dashboard comes from
   `i18n.js` or from the data.
10. Code never decides on the wording of a message; choices of the interface are
    compared by value, editable tables are read by column key.
11. Groups below the minimum size are never exported.
12. Named scales are expanded where the configuration is loaded; code that reads
    `questions.yaml` itself calls `utils.scales.expand_named_scales`.

## Pull requests

- One topic per pull request; describe what changes for users.
- Add or change tests with the code; a fix comes with a test that fails without it.
- Update the documentation in `docs/` when behaviour, settings or files change.
- Never commit files with access data, survey data of real respondents, databases or
  exports.
