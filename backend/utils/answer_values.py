# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""One text form for answer values, shared by the handlers and the statistics."""
import numpy as np


def answer_text(value):
    """
    An answer value as the text it stands for. A data import with type
    detection reads answer codes such as 0-10 as numbers, and as floats
    (10.0) when the column has gaps; configured options and labels are texts
    ("10"). Integral floats lose their ".0". Missing values stay missing.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return value
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, (float, np.floating)) and float(value).is_integer():
        return str(int(value))
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return value if isinstance(value, str) else str(value)
