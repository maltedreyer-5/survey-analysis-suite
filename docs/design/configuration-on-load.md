# Configuration on load

## The file stays as written

`questions.yaml` is the place where a survey is described; it is edited by hand, by the
editors of the suite, and compared and versioned outside the suite. The suite never
rewrites it to add something it derived. What it derives is computed each time the
configuration is loaded:

1. **Named scales** (`settings.scales`) are expanded: a question with `scale: <name>`
   gets the embedded options or scale, the direction and the polarity it would have
   with an embedded list; a dimension with `order: <name>` gets the list of values.
2. **Missing output languages** of the labels are filled from the translation memory.

Everything downstream (handlers, exports, reports, the dashboard export) reads only
the expanded configuration, so a named scale needs no support in each of them, and an
embedded list and a named scale give exactly the same analysis.

The check of the configuration runs on the file as written, because only there the
references to named scales are visible (an unknown scale name is an error), and then
on the expanded configuration for everything else.

Code that reads `questions.yaml` itself (scripts, notebooks) must call
`utils.scales.expand_named_scales(config)` before using it; otherwise a question with
a named scale has no options.

## Writes keep a snapshot

Every write of `questions.yaml` by the suite first saves the previous version under
`snapshots/` (the newest 10 are kept), so that every change can be undone. The editors
start from the stored configuration, not from the fields of a form, so settings that a
form does not show are kept.

## Limits

- When the suite writes the file, comments inside it are lost (the comment block at the
  top is kept); the YAML editor keeps the text exactly as typed.
- The expanded configuration exists only in memory; to see it, load it with
  `expand_named_scales` in a script.
