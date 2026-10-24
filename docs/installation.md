# Installation

## Requirements

- Python 3.12 (3.11 should work; only 3.12 is tested)
- `pip` and the `venv` module (on Debian and Ubuntu: `sudo apt install python3.12-venv`)
- About 1 GB of disk space for the virtual environment
- For the texts and the clustering of open answers: an OpenAI-compatible
  chat-completions endpoint and, if it needs one, an API key
- To view the dashboard: any current web browser

No database server, no web server and no administrator rights are needed; everything
is installed into the virtual environment and runs as the current user.

## Install

```bash
git clone https://github.com/maltedreyer-5/survey-analysis-suite.git
cd survey-analysis-suite
python3.12 -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

Check:

```bash
python -c "import gradio, pandas, scipy, docx; print('ok')"
```

## Settings: `backend/.env`

```bash
cp backend/.env.example backend/.env
```

The application reads `backend/.env` at start-up; environment variables of the shell
take precedence. Only the variables in `.env.example` are read.

| Variable | Meaning | Default |
|---|---|---|
| `GRADIO_SERVER_NAME` | address the web interface binds to | `127.0.0.1` |
| `GRADIO_SERVER_PORT` | port | `7861` |
| `GRADIO_ROOT_PATH` | path prefix behind a reverse proxy, e.g. `/survey` | empty |
| `SURVEY_ID` | survey active at start-up (directory under `backend/surveys/`) | the first survey found |
| `SURVEY_DATA_DIR` | database, translation memory, caches | `backend/data` |
| `SURVEY_OUTPUT_DIR` | exports and reports | `backend/outputs` |
| `LLM_BASE_URL` | base URL of the chat-completions API | `http://localhost:8000/v1` |
| `LLM_API_KEY` | API key | empty |
| `LLM_MODEL` | model name | `default` |
| `LLM_MAX_TOKENS` | maximum tokens of an answer | `4096` |
| `LLM_MAX_TOKENS_PARAM` | name of that limit in the request: `max_completion_tokens` or `max_tokens` (older servers) | `max_completion_tokens` |
| `LLM_TEMPERATURE` | sent only if set, then with every request | not sent |
| `LLM_TASK_TEMPERATURES` | `true`: task-specific temperatures of the code instead (only for models that accept any temperature; needs `LLM_TEMPERATURE`) | `false` |
| `LLM_EXTRA_PARAMS` | JSON object merged into every request, e.g. `{"reasoning_effort": "low"}` | empty |
| `LLM_TIMEOUT` | timeout of a request in seconds | `300` |
| `LLM_VERIFY_SSL` | `false` disables the certificate check (self-signed certificates only) | `true` |
| `MERMAID_RENDER_URL` | rendering service for diagrams in publication documents, e.g. `https://mermaid.ink/img/`; empty: diagrams stay code blocks | empty |

For OpenAI: `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY=<key>`,
`LLM_MODEL=<model>`. For a local vLLM server: its URL and the served model name.

## First start

```bash
cd backend
python app.py
```

Open <http://127.0.0.1:7861>. The log on the console names the active survey, the
LLM endpoint and the address. Check the connection to the language model with:

```bash
cd backend
python -c "from utils.llm import get_llm_client; print(get_llm_client().test_connection())"
```

`(True, ...)` means the endpoint answers.

## Example surveys

Two invented surveys are included (`ai_in_higher_education_de` and
`ai_in_higher_education_en`), with their data in `examples/data/`. Choose one in
**Setup → Survey selection**, import its CSV file in the **Import** tab and run the
**Pipeline**. `python examples/generate.py` writes both surveys and their data again.

## Behind a reverse proxy

The interface has no login. To make it reachable for others, keep
`GRADIO_SERVER_NAME=127.0.0.1` and put a reverse proxy with authentication in front
of it; set `GRADIO_ROOT_PATH` when the interface is served under a path prefix.

## Update

```bash
git pull
source .venv/bin/activate
pip install -r backend/requirements.txt
```

The database has a schema version. A database written by an incompatible version is
not changed; the suite refuses to open it and says so. Keep the old installation to
read old results, or start with a new `SURVEY_DATA_DIR` (see
[Operation](operations.md#start-over-with-an-empty-database)).

## Uninstall

Delete the repository directory. If `SURVEY_DATA_DIR` or `SURVEY_OUTPUT_DIR` point
elsewhere, delete those directories too. See [System impact](system-impact.md) for
everything else the suite or its libraries write.

## Limits

- Only Python 3.12 on Linux is tested regularly; Windows and macOS are expected to
  work, since the suite uses no platform-specific code, but are not part of the checks.
- The language model is the slowest part: a pipeline run of a survey with a few hundred
  open answers makes a few hundred requests. A local model needs a GPU to be fast enough.
- The suite needs no internet connection except for the language-model endpoint and,
  if set, `MERMAID_RENDER_URL`.
