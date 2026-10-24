# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Survey configuration generated from a LimeSurvey structure file (LSS) and the
columns of the exported data (decision 3.6.11). The LSS adapter
(utils/structure_lss.py) reads the structure into the format-neutral model,
utils/survey_structure.build_config turns it into the configuration, as for
every other input format (decision 3.6.12).
"""
from typing import List, Tuple

from utils.structure_lss import lss_structure
from utils.survey_structure import build_config as _build


def build_config(survey_data, data_columns: List[str]) -> Tuple[dict, dict]:
    return _build(lss_structure(survey_data, data_columns))
