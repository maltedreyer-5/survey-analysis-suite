# Security

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub: **Security → Report a
vulnerability** in this repository (private vulnerability reporting). Do not open a
public issue for a vulnerability. Include the version, the steps to reproduce, and
what an attacker gains. You will get an answer as soon as the report has been read;
this is a project maintained in spare time, without guaranteed response times.

## Intended use

The suite is meant to run on the computer of the person who analyses a survey
(a researcher, a member of staff of a university), for one user at a time, started
from a terminal. The web interface listens on `127.0.0.1`. Answers are sent to a
language-model API chosen by that person (for example the OpenAI API, or a model in
their own network).

## What is protected, and how

| Asset | Threat | Protection |
|---|---|---|
| Survey answers, including open answers (possibly personal data) | access by other users of the network | the interface binds to `127.0.0.1`; no share link is ever opened; Gradio's usage statistics and version check are off |
| | disclosure through exports | groups below the minimum size (default 5) are not exported; the dashboard contains only aggregated results and the cluster examples chosen by the language model |
| API key of the language model | disclosure | read from `backend/.env` or the environment, sent only in the `Authorization` header to `LLM_BASE_URL`; not written to logs, exports or the usage records |
| Users of the exports | spreadsheet formulas in CSV exports (cells starting with `=`, `+`, `-`, `@`, tab or carriage return) | such cells are neutralised before they are written |
| Viewers of the dashboard | script injection through answer texts | texts from the data are inserted as text, never as HTML (tested); the page loads no external scripts |
| The computer running the suite | malicious structure files (LimeSurvey XML) | parsed with the standard XML parser of Python, which resolves no external entities and limits entity expansion (expat 2.6) |

## Not in scope

These are deliberate limits or wrong use, not vulnerabilities:

- **Exposing the web interface.** It has no login and no roles, and it is not built to
  be reachable by others. Binding it to another address (`GRADIO_SERVER_NAME`) without an
  authenticating reverse proxy in front of it gives everyone who can reach it full
  access to all data and settings. This is a configuration error.
- **Several users** sharing one running instance. The state is shared; one user sees
  and changes the work of another.
- **What the language-model provider does with the data.** Answers, question texts and
  labels are sent to `LLM_BASE_URL` by design. Whether this is allowed for a survey is
  a decision under data-protection rules, outside the suite. Use a model in your own
  network to keep the data local.
- **Prompt injection through answers.** Respondents can write text that tries to steer
  the language model. The effect is limited to texts the model writes (cluster names,
  descriptions, summaries): numbers, tests and which differences are reported are
  computed by the code. Review the texts before publishing.
- **Untrusted input files** beyond the XML case above: very large or malformed data
  files can exhaust memory or stop an import. Import only files from the survey tool
  you use.
- **Local attackers** with access to the user account or the file system. The database,
  the exports and `backend/.env` are plain files protected only by file permissions.
- **`LLM_VERIFY_SSL=false`** turns off the certificate check of the language-model
  connection; use it only for a server with a self-signed certificate in a trusted
  network.
- **`MERMAID_RENDER_URL`** sends the source code of diagrams of publication documents
  to the service named there; leave it empty to keep them local.

## Dependencies

The Python dependencies are listed with version ranges in
`backend/requirements.txt`; the libraries of the dashboard are included at fixed
versions in `dashboard/vendor/`. Updates of dependencies are made as ordinary changes
and pass the same checks (`CONTRIBUTING.md`).
