# System impact

What the suite changes on the system it runs on, which connections it opens, and how
to undo each change. The suite installs no service, changes no system setting, needs
no administrator rights, and writes nothing outside the places listed here.

## Files and directories

| What | Where (default) | Written by | Undo |
|---|---|---|---|
| Python environment | `.venv/` in the repository | installation | delete the directory |
| Settings | `backend/.env` | you | delete the file |
| Database: imports, answers, analyses, clusters, Workbench changes, texts, translation memory | `backend/data/survey_data.db` (`SURVEY_DATA_DIR`) | import, analysis, Workbench, translations | delete the file; a new empty one is created at the next start |
| Log of language-model use | `backend/data/llm_usage.jsonl` | every pipeline run | delete the file |
| Cache of rendered diagrams | `backend/data/diagrams/` | publication documents, only with `MERMAID_RENDER_URL` | delete the directory |
| Exports: Word reports, dashboard data, JSON, CSV, publication documents | `backend/outputs/` (`SURVEY_OUTPUT_DIR`), one folder per run | pipeline, re-export, Export tab | delete the folders |
| Configuration of a survey | `backend/surveys/<id>/questions.yaml` | Config assistant, segmentation editor, numeric clean-up, structure import, YAML editor | restore a snapshot (below) |
| Previous versions of the configuration | `backend/surveys/<id>/snapshots/` | every write of `questions.yaml` | delete the directory; the suite keeps the newest 10 and removes older ones itself |
| Offline data of the dashboard | `dashboard/js/*-data.js` | `build_offline.py` | delete the files, or `git checkout dashboard/js/` |
| Byte code | `__pycache__/` directories in the repository | Python | delete them |

Written by the libraries the suite uses:

| What | Where | Undo |
|---|---|---|
| Uploaded files (copies of the data and structure files) | `<temp>/gradio/` (`GRADIO_TEMP_DIR`; `<temp>` is the system temporary directory, e.g. `/tmp`) | delete the directory; set `GRADIO_TEMP_DIR` to keep uploads elsewhere |
| Font cache of matplotlib | `~/.cache/matplotlib/` (`MPLCONFIGDIR`) | delete the directory |

The data directory holds the answers of the respondents, including open answers.
Protect it like the original data file, and delete it when the analysis is finished
and no longer needed.

## Network

| Connection | When | Contents |
|---|---|---|
| Listening socket `127.0.0.1:7861` | while the interface runs | the web interface; `GRADIO_SERVER_NAME`/`GRADIO_SERVER_PORT` change it |
| Language-model endpoint (`LLM_BASE_URL`) | pipeline, Workbench reclustering, Config assistant, translations | question texts, answer options, **answer texts including open answers**, cluster examples, statistical facts |
| Rendering service (`MERMAID_RENDER_URL`) | only if set, when publication documents contain Mermaid diagrams | the source code of the diagrams |

Nothing else:

- The interface turns off the usage statistics and the version check of Gradio, and
  uses system fonts; opening it in a browser makes no request to another host
  (measured with a browser that records every request).
- The dashboard contains all its libraries and its font; it makes no external
  requests.
- No share link (Gradio tunnel) is ever opened.

To avoid sending answers to an external service, use a language model in your own
network (for example vLLM) and set `LLM_BASE_URL` to it.

## Undo everything

```bash
rm -rf survey-analysis-suite              # repository, environment, data, outputs (default locations)
rm -rf "${TMPDIR:-/tmp}/gradio"           # uploads (if no one else uses Gradio)
rm -rf ~/.cache/matplotlib                # font cache (if no one else uses matplotlib)
```

If `SURVEY_DATA_DIR` or `SURVEY_OUTPUT_DIR` point elsewhere, delete those directories
too. Published dashboards and documents are copies; remove them where you put them.

## Restore a configuration

```bash
ls -t backend/surveys/<id>/snapshots/
cp backend/surveys/<id>/snapshots/<snapshot>.yaml backend/surveys/<id>/questions.yaml
```

or **Config assistant → Snapshots → Restore the last snapshot**.

## Limits

- The suite does not encrypt the database or the exports; use the protection of the
  file system.
- It does not delete anything on its own except older snapshots and the cache it
  rebuilds; old imports and exports stay until you delete them.
- It cannot control what the language-model provider does with the requests;
  assess the provider under your data-protection rules.
- Uploaded copies in the Gradio directory are not removed when the interface stops.
