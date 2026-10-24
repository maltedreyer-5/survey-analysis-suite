# Operation

Step-by-step instructions for recurring tasks. Every task ends with the commands to
check the result. The commands assume the repository root as working directory and the
activated virtual environment (`source .venv/bin/activate`); paths are the defaults
(`backend/data`, `backend/outputs`) unless `SURVEY_DATA_DIR` or `SURVEY_OUTPUT_DIR`
are set.

## Start and stop the web interface

```bash
cd backend && python app.py
```

Stop it with `Ctrl+C`. Check that it runs and where:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7861/    # 200
```

## Set up a new survey

1. Create `backend/surveys/<survey_id>/` with a `survey.yaml` (`id` = directory
   name) and a `questions.yaml`. Start from a copy of an example survey; see
   [Configuration](configuration.md).
2. Restart the interface, or open **Setup → Survey selection**, click *Refresh list*,
   choose the survey and click *Switch to the chosen survey*.
3. If you have the structure file of the questionnaire (LimeSurvey `.lss`, Qualtrics
   `.qsf`), import it together with the data in the **Import** tab; it completes the
   question texts and answer options.
4. Open **Configuration** and read the errors and warnings of the check.

Check the configuration without the interface:

```bash
cd backend
python - <<'PY'
import yaml
from services.config_validation import validate_survey_config
cfg = yaml.safe_load(open("surveys/<survey_id>/questions.yaml", encoding="utf-8"))
errors, warnings = validate_survey_config(cfg)
print("errors:", errors); print("warnings:", warnings)
PY
```

## Import the answers

1. **Import** tab: choose the data file (CSV, `.xlsx`, `.xls`, `.sav`) and optionally the
   structure file; for Excel with several sheets, choose the sheet.
2. Read the import report: number of rows, columns matched with the configuration,
   columns in the configuration that the data lack.

Check what was stored:

```bash
python - <<'PY'
import sqlite3
con = sqlite3.connect("backend/data/survey_data.db")
for row in con.execute("SELECT id, survey_id, filename, total_responses, imported_at FROM imports ORDER BY id DESC LIMIT 5"):
    print(row)
PY
```

Every import is kept; the analysis uses the newest import of the active survey.

## Run the pipeline

1. **Pipeline** tab: choose the steps (all are on by default) and the languages,
   click *Complete pipeline*.
2. The status lists every step with its result, including the number of requests to
   the language model.

The results go into a new folder `backend/outputs/full_pipeline_<date>_<time>/`.
Check it:

```bash
ls backend/outputs/full_pipeline_*/ | tail -20
python -c "import json,glob; f=sorted(glob.glob('backend/outputs/full_pipeline_*/llm_usage.json'))[-1]; print(f, json.load(open(f))['total_calls'], 'LLM calls')"
```

Expected files: `report_<lang>_<time>.docx` per language, `dashboard_data.json`,
`themes.json`, `correlations.json`, `segment_clusters.json`,
`segment_insights.json`, `survey_structure.json`, `llm_usage.json`.

## Correct the clusters and export again

1. **Workbench → Clusters**: edit names and descriptions in the table and *Save
   changes*; *Add a cluster*; *Delete a cluster* with a cluster for its items;
   *Recluster with these names* distributes all items over the names in the table.
   **Workbench → Items**: correct the text in *Extracted*, or type another cluster's name
   in *Cluster* to move the item, then *Save changes*. **Workbench → Normalization**:
   unify spelling variants.
2. Every saved change shows a banner *re-export needed*.
3. **Pipeline** tab, *Re-export after Workbench changes*: optionally let the language
   model reassign the items of changed clusters; Word reports and dashboard data are
   written again.

Check:

```bash
ls -d backend/outputs/reexport_* | tail -1
```

## Publish the dashboard

1. Copy the folder `dashboard/` to the target (any web server, or a local folder).
2. Copy the JSON files of the run into its `data/` folder:

   ```bash
   cp backend/outputs/full_pipeline_<time>/*.json <target>/data/
   ```

3. Only for opening the page from the file system (`file://`): convert the JSON files
   into script files, because browsers do not let a local page load JSON:

   ```bash
   cd <target> && python build_offline.py
   ```

Check that every data file is valid JSON and that the page needs nothing from outside:

```bash
python -c "import json,glob; [json.load(open(f)) for f in glob.glob('<target>/data/*.json')]; print('json ok')"
grep -Eo 'src="https?://[^"]*"|href="https?://[^"]*"' <target>/index.html || echo "no external resources"
```

## Build publication documents

Publication documents are Word documents built from Markdown sources of the survey:
a cover, an introduction, a method note, or the Word report of the pipeline with a
cover put in front. They are optional and need a `documents.yaml` in the survey
directory; the example surveys have one.

1. Run the pipeline first if a document extends its Word report (`existing_docx`).
2. **Export** tab, section *Create publication documents*: choose the languages and
   click *Create all documents*, or *Update missing/changed only* to build only what
   is missing or changed.
3. Paragraphs are translated once and cached; *Clear the translation cache* (per
   document and language) forces a new translation.

Check:

The documents are written into `backend/outputs/` as `<filename>_<LANG>.docx`:

```bash
ls -t backend/outputs/*.docx | head
```

## Correct translations

**Import** tab, *Review and edit translations*: *Load translations* lists the
translations into a target language; corrected texts are saved and used from then on. Labels written in
`questions.yaml` always win over translations.

## Restore an earlier configuration

**Config assistant** tab, *Snapshots*: *Refresh snapshots* lists the saved versions;
*Restore the last snapshot* restores the newest one (the current file is saved as a
snapshot first, so the restore can be undone). By hand:

```bash
ls -t backend/surveys/<survey_id>/snapshots/ | head
cp backend/surveys/<survey_id>/snapshots/<snapshot>.yaml backend/surveys/<survey_id>/questions.yaml
```

## Back up and restore

Everything the suite keeps between runs is in the data directory (database with
imports, analyses, Workbench changes and translation memory) and in the survey
directories (configuration and snapshots):

```bash
tar czf backup_$(date +%Y%m%d).tar.gz backend/data backend/surveys
```

Restore by unpacking into a stopped installation of the same version. The exports in
`backend/outputs` can be produced again from the database.

## Start over with an empty database

Stop the interface and move the database aside; a new one is created at the next
start:

```bash
mv backend/data/survey_data.db backend/data/survey_data.db.bak
```

This is also the way out when a database was written by an incompatible version: the
suite refuses to open it and names the file.

## Check the use of the language model

Every run records its requests: `llm_usage.json` in the output folder of the run and
one line per run in `backend/data/llm_usage.jsonl`, with the number of calls, tokens
and the calls per task.

```bash
tail -n 3 backend/data/llm_usage.jsonl
```

## Limits

- The interface handles one survey at a time and is meant for one user at a time;
  two people working on the same survey at once overwrite each other's Workbench
  changes.
- A pipeline run cannot be paused; if it is interrupted, start it again. Summaries and
  translations that were already stored are reused.
- Results of the language model can differ between runs; a new run can produce other
  cluster names. Check the clusters in the Workbench before publishing.
- The backup above does not include the exports; keep the published files separately.
