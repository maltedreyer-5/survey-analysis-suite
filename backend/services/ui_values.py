# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Internal values of interface choices that the code acts on.

A choice is offered as a (label, value) pair: the code compares only the
value, so the label shown can change (for instance when the interface is
translated) without changing behaviour. The values are sentinels that cannot
collide with a segment value, a cluster name or a document id.
"""

NO_SEGMENTATION = "__none__"   # segmentation dropdown: no segmentation
ALL = "__all__"                # filters: everything (all segments, clusters, documents, languages)

LABEL_NO_SEGMENTATION = "No segmentation"
LABEL_ALL = "All"
LABEL_ALL_COMPARE = "All (comparison)"


def choice_all(label: str = LABEL_ALL) -> tuple:
    """The (label, value) pair for "everything"."""
    return (label, ALL)


def pairs(values) -> list:
    """Plain values as (label, value) pairs with the value as label."""
    return [(v, v) for v in values]
