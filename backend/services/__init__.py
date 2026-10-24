# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Service layer of the survey analysis tool.

Holds the business logic behind the interface, separate from `app.py`:
- the interface definition stays light and readable
- the business logic is testable (services take `state` as an explicit argument)

Conventions for service functions:
- the first argument is always `state: AppState` (or a compatible object),
  which makes the functions testable with a stub state.
- optional `progress=None` parameters use a callback protocol
  (callable(fraction: float, desc: str) -> None) instead of importing
  gradio.Progress directly.
- return values are plain data (strings, dicts, tuples), nothing Gradio-specific.
- side effects on `state.*` are documented in the docstrings.
"""

from . import translations
from . import pipeline
from . import analysis
from . import workbench
from . import publish
from . import themes
from . import config_io
from . import config_assistant
from . import survey_status
from . import numeric_workbench
from . import segmentation_editor

__all__ = [
    'translations', 'pipeline', 'analysis', 'workbench',
    'publish', 'themes',
    'config_io', 'config_assistant', 'survey_status',
    'numeric_workbench', 'segmentation_editor',
]
