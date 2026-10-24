# Survey Analysis Suite

The Survey Analysis Suite analyses the results of a questionnaire survey and publishes
them in several languages. It reads the answers (CSV, Excel, SPSS) and optionally the
questionnaire structure (LimeSurvey `.lss`, Qualtrics `.qsf`), analyses every question
according to its type, compares groups of respondents, clusters open answers into
topics with a language model, and produces:

- Word reports per output language, with charts, tables and short summaries;
- an interactive, self-contained web dashboard (static files, no server needed);
- JSON and CSV exports of all results.

A survey is described once in a configuration file (`questions.yaml`): question types,
answer options, scales, groups of respondents and languages. The web interface (Gradio)
guides through import, analysis, manual corrections of the clusters, and export.

## Quick start

```bash
git clone https://github.com/maltedreyer-5/survey-analysis-suite.git
cd survey-analysis-suite
python3.12 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env # then set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
cd backend && python app.py
```

Open <http://127.0.0.1:7861>. Two example surveys (German and English, invented data)
are included: import `examples/data/ai_in_higher_education_en.csv` in the **Import**
tab and run the **Pipeline** tab. Details: [Installation](docs/installation.md),
[Operation](docs/operations.md).

## Requirements

- Python 3.12 (3.11 should work; only 3.12 is tested)
- An OpenAI-compatible chat-completions endpoint (for example OpenAI or a local vLLM
  server) for the clustering of open answers and the texts. Without a language model the statistical analysis, the charts and the exports
  still work; texts that a model would write are left out.
- About 1 GB of disk space for the Python environment

## Security notice

The web interface has **no login and no roles**. It binds to `127.0.0.1` by default.
Do not expose it to a network without an authenticating reverse proxy in front of it.
Answer texts, including open answers, are sent to the configured language-model
endpoint; assess this under your data-protection rules before using an external
service. See [System impact](docs/system-impact.md) and `SECURITY.md`.

## Documentation

The documentation starts at [docs/index.md](docs/index.md):

- [Tutorial](docs/tutorial.md): one survey from the export of the survey tool to the
  published results
- [User guide](docs/user-guide.md): the web interface, tab by tab
- [Methods](docs/methods.md): bases, formulas, tests and thresholds of every result
- [Charts](docs/charts.md) and [Dashboard guide](docs/dashboard-guide.md): how to read
  the reports and the dashboard
- [Features](docs/features.md): what the suite does, question types, groups,
  statistics, exports, languages
- [Configuration](docs/configuration.md): `survey.yaml`, `questions.yaml`, named scales,
  segmentation, languages
- [Installation](docs/installation.md)
- [Operation](docs/operations.md): step-by-step instructions for recurring tasks
- [Architecture](docs/architecture.md) and the [dashboard data format](docs/dashboard-data.md)
- [System impact](docs/system-impact.md): what the suite changes on a system, and how to undo it
- [Glossary](docs/glossary.md)

## Third-party components

The suite uses these components; each keeps its own licence.

| Component | Use | Licence |
|---|---|---|
| Gradio | web interface | Apache-2.0 |
| pandas, NumPy | data handling | BSD-3-Clause |
| SciPy | statistical tests | BSD-3-Clause |
| matplotlib | charts in the Word reports | Matplotlib licence (PSF-based) |
| python-docx, docxcompose | Word documents | MIT |
| PyYAML | configuration files | MIT |
| python-dotenv | `.env` file | BSD-3-Clause |
| httpx | requests to the language model | BSD-3-Clause |
| langdetect | language detection of open answers | Apache-2.0 |
| openpyxl, xlrd | Excel import | MIT, BSD-3-Clause |
| pyreadstat | SPSS import | Apache-2.0 |
| Alpine.js (dashboard) | dashboard interactivity | MIT |
| Plotly.js (dashboard) | dashboard charts | MIT |
| D3.js (dashboard) | association network | ISC |
| Tailwind CSS (dashboard, generated) | dashboard styles | MIT |
| DM Sans (dashboard) | font | SIL Open Font License 1.1 |

The dashboard ships these libraries and the font in `dashboard/vendor/` with their
licence texts (`dashboard/vendor/LICENSES.md`); it makes no external requests.

## Licence

MIT, see `LICENSE`.
