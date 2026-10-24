# Statistics

## What is tested

- **Group differences**: every segmentation dimension against every analysis variable
  of a question (each option of a choice question, each item of a matrix, the values of
  a numeric question).
- **Associations** between two questions.

The test follows the measurement level of the variables:

| Variables | Test | Effect size |
|---|---|---|
| both ordinal or numeric | Kendall's tau-b | tau-b |
| numeric × nominal or binary | Kruskal-Wallis | epsilon squared |
| all other combinations, including a nominal group × an ordered scale | chi-square; Fisher's exact test for a 2×2 table with small expected counts | Cramér's V, bias-corrected |

A chi-square test whose expected counts are too small (more than 20 % of the cells
below 5, or any below 1) is not used; the pair counts as not testable and is reported
as such.

## Base of every test

Respondents who did not answer a question are not part of its base, and neither are
evasive answers ("don't know", "no answer"). Tests run on analysis variables derived
from the answers, never on counts of mentions: counting mentions would weigh
respondents who name many things more than others.

## Many tests at once

All tests of one question form one family; their p-values are corrected with the
Benjamini-Hochberg procedure before anything is said. The false discovery rate (the
expected share of false findings among the reported ones) stays below the significance
level (0.05). The price is power: with many tests, small real differences need more
respondents to be reported.

## When a difference is reported

A group difference becomes a finding only if it is significant after the correction,
has at least a weak effect, and the shares of the highest and the lowest group differ
by at least 15 percentage points; at most three findings per question and dimension
are reported. Smaller significant differences are counted in the method record and not
listed one by one. Reason: with many groups and options, the number of significant but
small differences is large; listing all of them makes the relevant ones hard to find.

"No difference" is only said where a test was possible; where groups were too small,
the report says the question was not testable.

## Minimum sizes

| What | Minimum |
|---|---|
| group shown at all (privacy) | 5 respondents (`sample_warnings.minimum_threshold`) |
| group marked as small | 10 (`sample_warnings.warning_threshold`) |
| association between two questions | 20 respondents with both answers, 5 per category |
| own clusters of a group | 15 (`specific_clusters_min_n`) |

## Limits

- The tests assume independent respondents; panel data or repeated answers of the
  same person are outside their assumptions.
- Groups are compared with each other, not with a population: the results describe the
  respondents, and weighting to a population is not supported.
- Segment-specific clusters are formed per group and are not tested against each other.
- Ranking: an item a respondent did not rank receives no points from that respondent;
  the score therefore mixes how often an item was ranked and how high.
