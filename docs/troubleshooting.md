# Troubleshooting

Symptoms, their usual causes and what to do. The log of the console (where `python
app.py` runs) has the details of every error.

## Language model

| Symptom | Cause and remedy |
|---|---|
| `(False, 'LLM connection error: …Connection refused')` in the connection test ([Installation](installation.md#first-start)); the pipeline reports "Translations: LLM not available, skipped" | `LLM_BASE_URL` points to no running server; check the URL and the network |
| HTTP 401 or 403 | `LLM_API_KEY` missing or wrong |
| HTTP 400 mentioning `max_tokens` or `max_completion_tokens` | the server expects the other parameter: set `LLM_MAX_TOKENS_PARAM=max_tokens` (older servers) or `max_completion_tokens` |
| HTTP 400 mentioning `temperature` | the model accepts no temperature: remove `LLM_TEMPERATURE` (no temperature is sent then) |
| certificate errors (`CERTIFICATE_VERIFY_FAILED`) | a server with a self-signed certificate: install its certificate, or set `LLM_VERIFY_SSL=false` in a trusted network |
| requests time out | long answers or a slow model: raise `LLM_TIMEOUT` |
| empty or cut-off texts (finding texts, summaries), clustering repeated with a reasoning model | the model used the answer limit for thinking: raise `LLM_MAX_TOKENS` (16000 or more), or lower the effort with `LLM_EXTRA_PARAMS` (`{"reasoning_effort": "low"}`) |
| the Config assistant reports "No suggestions created" | the model answered with no usable suggestion, or all settings are set already (*Scope*) |

## Import and configuration

| Symptom | Cause and remedy |
|---|---|
| *Import status* lists configured columns missing in the data | the column names of the data and of `questions.yaml` differ (renamed in the survey tool, other export option); correct `columns` |
| answers appear as codes (`A1`, `A2`) | the data has answer codes but no structure file was imported; import the `.lss` / `.qsf` with the data |
| a question has no scale metrics | the answers are not configured as an ordered scale: `ordinal: true` or a named scale ([Recipes](recipes.md)) |
| a 0–10 question has no NPS | `nps: true` is missing |
| a group is missing in results | it has fewer respondents than `minimum_threshold` |
| answers counted as a separate option although they mean the same | spelling variants: **Workbench → Normalization**, or unify them in the survey tool |
| errors in the *Status* of the Configuration tab | the check names the place; see [Configuration](configuration.md) |

## Start-up and database

| Symptom | Cause and remedy |
|---|---|
| "address already in use" | another program uses the port: set `GRADIO_SERVER_PORT` |
| "The database … was written by another version of this tool" | the database has another schema version and is not changed: move it aside or set `SURVEY_DATA_DIR` to a new directory ([Operation](operations.md#start-over-with-an-empty-database)) |
| the survey is missing in *Choose a survey* | its directory lacks `survey.yaml` with an `id` or `questions.yaml`; *Refresh list* after adding it |

## Reports and dashboard

| Symptom | Cause and remedy |
|---|---|
| the table of contents of a Word report is empty | Word fills it when it is updated: Ctrl+A, F9 |
| texts of a report in the wrong language | the language was not among the output languages of the survey when the pipeline ran (`settings.survey_metadata.languages`); run the pipeline again |
| the dashboard opened from the file system shows no data | browsers do not let a local page load JSON: run `python build_offline.py` in the dashboard folder ([Operation](operations.md#publish-the-dashboard)) |
| the dashboard lacks group differences after a re-export | the JSON files of the re-export folder were not copied completely; copy all `*.json` of the folder |
| cluster names and texts change between runs | the language model is not deterministic; correct the clusters in the Workbench and use the re-export |

## Limits

The suite reports errors in the status fields and in the log; it does not repair data
files or configurations on its own. A configuration error stops nothing that does not
depend on it: the other questions are analysed.
