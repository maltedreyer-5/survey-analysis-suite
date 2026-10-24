# Languages and translations

## Source and output languages

The **source language** is the language of the answers and of the configuration; it is
German or English, because the prompts of the language model exist for these two. The
**output languages** are free; without a setting, only the source language is produced.
Fixed texts of the reports, the exports and the dashboard exist in German, English,
French and Italian; for other output languages they fall back to English, while the
texts of the survey are translated.

## Open answers

Open answers in other languages are translated into the source language before they
are split and clustered, so that one topic does not become two clusters because it was
written in two languages. The original answers stay in the database.

## Labels of the configuration

Question texts, answer options, scale points and group names are translated into the
output languages. Short labels are hard to translate on their own ("Gut" can be "good",
"fine", "estate"); therefore every list is translated as a whole, in one request, with:

- the question text;
- the meaning of the scale (`context` of a named scale);
- a note that the texts are the points of one scale in their order, or the answer
  options of one question.

Labels written in `questions.yaml` are never overwritten; only missing languages are
filled. A survey that brings its own translations (for example from a LimeSurvey file
with several languages) keeps them.

## Translation memory

Every translation is stored with its source text, its languages and its scope (the
question, `scale:<name>`, `dimension:<id>`). The same text in another question is
translated again, because its meaning can differ. Loading the configuration fills
missing languages from the memory without asking the model; the pipeline asks the model
only for what is missing. Corrections in the translation table of the **Import** tab
replace the stored translation.

Publication documents have their own cache per paragraph; a changed paragraph is
translated again.

## Limits

- Translations by a language model can be wrong, especially for technical terms;
  `settings.glossary` and `settings.keep_terms` fix the translation of terms, the
  translation table corrects single texts.
- Language detection of open answers knows German, French and English; answers in
  other languages are treated as the source language and not translated.
- The web interface is in English only.
