# Dashboard

A static web page that shows the results of a survey: filters for the groups of
respondents, cross filters, a view per theme, group comparisons, deviations from the
average, and the associations between questions. It needs no server and makes no
external requests; Alpine.js, Plotly.js, D3.js, the built Tailwind stylesheet and the
DM Sans font are in `vendor/` with their licences.

| Path | Contents |
|---|---|
| `index.html` | the page |
| `js/app.js` | application state and views (Alpine.js) |
| `js/charts.js` | charts (Plotly.js) |
| `js/i18n.js` | texts of the page in German, English, French and Italian |
| `data/*.json` | the data, written by the dashboard export of the backend |
| `build_offline.py` | converts `data/*.json` into `js/*-data.js` for opening the page from the file system |
| `tests/` | tests of the page logic (Node.js) |

The data format is described in [docs/dashboard-data.md](../docs/dashboard-data.md);
publishing is described in [docs/operations.md](../docs/operations.md#publish-the-dashboard).
`data/` contains the demo data of the English example survey, with German labels
from the German example survey (`python examples/make_demo_dashboard.py` writes it
again).

After adding Tailwind classes to `index.html` or `js/*.js`, rebuild the stylesheet as
described in `vendor/LICENSES.md`.
