# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Two invented example surveys on the use of AI at universities, one German
and one English, with synthetic answers. They cover every question
type and every kind of segmentation:

- single choice (nominal, ordinal with "don't know", NPS 0-10), multiple
  choice (yes/no) with "other, please specify", Likert matrix, ranking,
  number, several numbers with a constant sum, date, free text in one field
  and in several fields;
- segmentation by identity, value mapping, numeric buckets and a data column
  without a question, a cross filter, segment-specific clusters.

The answers follow a few planted patterns (students rate the training offered
lower, recommend less often and put costs first, ...), so group differences and associations
have something to find. Fixed seed: the files are reproducible.

Run from the repository root: python examples/generate.py
Writes backend/surveys/ai_in_higher_education_<de|en>/ (survey.yaml, questions.yaml),
examples/data/ai_in_higher_education_<de|en>.csv, and the same surveys as a LimeSurvey
export in examples/limesurvey/ (structure file .lss and responses .csv, see limesurvey.py).
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
N = 240

T = {
    "de": {
        "title": "Einsatz von KI an Hochschulen (Beispiel)",
        "description": "Erfundene Beispielumfrage zum Einsatz von KI-Werkzeugen in Studium, Lehre, Forschung und Verwaltung, synthetische Antworten.",
        "scale_context": {"satisfaction": "Zufriedenheit, von sehr zufrieden bis sehr unzufrieden",
                          "quality": "Bewertung der Qualität, von sehr gut bis sehr schlecht"},
        "context": "Erfundene Befragung von Studierenden und Beschäftigten einer Hochschule zum Einsatz von KI-Werkzeugen in Studium, Lehre, Forschung und Verwaltung.",
        "role": ("Ihre Rolle an der Hochschule?", ["Studium", "Lehre und Forschung", "Verwaltung und Technik"]),
        "faculty": ("Ihre Fakultät?", ["Informatik", "Physik", "Geschichte", "Soziologie"]),
        "area": ("Fachbereich", {"Informatik": "MINT", "Physik": "MINT", "Geschichte": "Geistes- und Sozialwissenschaften",
                                 "Soziologie": "Geistes- und Sozialwissenschaften"}),
        "age": ("Ihr Alter (Jahre)?", "Altersgruppe", [("unter 25", None, 25), ("25 bis 39", 25, 40), ("40 und älter", 40, None)]),
        "site": "Standort",
        "sites": ["Nord", "Süd"],
        "sat": ("Wie zufrieden sind Sie mit den KI-Angeboten Ihrer Hochschule?",
                ["Sehr zufrieden", "Eher zufrieden", "Eher unzufrieden", "Sehr unzufrieden", "Weiß nicht"]),
        "nps": "Wie wahrscheinlich ist es, dass Sie die KI-Angebote Ihrer Hochschule weiterempfehlen? (0–10)",
        "used": ("Welche KI-Werkzeuge haben Sie im letzten Semester genutzt?",
                 ["Chatbots (z. B. für Texte)", "Übersetzung", "Programmierhilfe", "Bildgenerierung", "Sonstiges"]),
        "yes": "Ja", "no": "Nein",
        "other_answers": ["Transkription", "Literaturrecherche", "Folien erstellen", "Datenanalyse", "Zusammenfassungen"],
        "rate": ("Wie bewerten Sie …", ["die Verlässlichkeit der Ergebnisse", "den Datenschutz", "die Schulungsangebote"],
                 ["Sehr gut", "Gut", "Schlecht", "Sehr schlecht", "Weiß nicht"]),
        "rank": ("Was ist Ihnen beim Einsatz von KI am wichtigsten? (Rang 1 bis 3)",
                 ["Datenschutz", "Kosten", "Qualität der Ergebnisse", "Einfache Bedienung"]),
        "budget": ("Wie sollte die Hochschule ein KI-Budget von 100 Punkten verteilen?", ["Lehre", "Forschung", "Verwaltung"]),
        "visit": "Wann haben Sie zuletzt eine KI-Schulung besucht?",
        "improve": "Was sollte die Hochschule beim Einsatz von KI vor allem verbessern?",
        "reasons": "Nennen Sie bis zu drei Gründe, warum Sie KI-Werkzeuge nutzen oder nicht nutzen.",
        "texts": {
            "Datenschutz": ["Unklar, was mit meinen Daten passiert", "Datenschutzkonforme Werkzeuge anbieten",
                            "Keine personenbezogenen Daten eingeben dürfen"],
            "Prüfungsregeln": ["Klare Regeln für Prüfungen", "Unsicherheit, was als Täuschung gilt",
                               "Einheitliche Vorgaben der Fakultäten"],
            "Schulungen": ["Mehr Schulungen zum Prompten", "Workshops für Lehrende", "Beispiele für gute Nutzung"],
            "Lizenzen und Kosten": ["Campuslizenz statt privater Abos", "Bezahlversionen sind zu teuer",
                                    "Kostenloser Zugang für alle"],
            "Qualität": ["Antworten sind oft falsch", "Quellen werden erfunden", "Ergebnisse müssen geprüft werden"],
        },
    },
    "en": {
        "title": "Use of AI at universities (example)",
        "description": "Invented example survey on the use of AI tools in studies, teaching, research and administration, synthetic answers.",
        "scale_context": {"satisfaction": "satisfaction, from very satisfied to very dissatisfied",
                          "quality": "rating of quality, from very good to very poor"},
        "context": "Invented survey of students and staff of a university about the use of AI tools in studies, teaching, research and administration.",
        "role": ("Your role at the university?", ["Student", "Teaching and research", "Administration and technical"]),
        "faculty": ("Your faculty?", ["Computer science", "Physics", "History", "Sociology"]),
        "area": ("Field", {"Computer science": "STEM", "Physics": "STEM", "History": "Humanities and social sciences",
                           "Sociology": "Humanities and social sciences"}),
        "age": ("Your age (years)?", "Age group", [("under 25", None, 25), ("25 to 39", 25, 40), ("40 and older", 40, None)]),
        "site": "Site",
        "sites": ["North", "South"],
        "sat": ("How satisfied are you with the AI services of your university?",
                ["Very satisfied", "Rather satisfied", "Rather dissatisfied", "Very dissatisfied", "Don't know"]),
        "nps": "How likely are you to recommend the AI services of your university? (0-10)",
        "used": ("Which AI tools did you use last term?",
                 ["Chatbots (e.g. for writing)", "Translation", "Coding assistance", "Image generation", "Other"]),
        "yes": "Yes", "no": "No",
        "other_answers": ["Transcription", "Literature search", "Creating slides", "Data analysis", "Summaries"],
        "rate": ("How do you rate …", ["the reliability of the results", "data protection", "the training offered"],
                 ["Very good", "Good", "Poor", "Very poor", "Don't know"]),
        "rank": ("What matters most to you when using AI? (rank 1 to 3)",
                 ["Data protection", "Costs", "Quality of results", "Ease of use"]),
        "budget": ("How should the university split an AI budget of 100 points?", ["Teaching", "Research", "Administration"]),
        "visit": "When did you last attend an AI training?",
        "improve": "What should the university improve most regarding the use of AI?",
        "reasons": "Give up to three reasons why you use or do not use AI tools.",
        "texts": {
            "Data protection": ["Unclear what happens to my data", "Offer tools that comply with data protection",
                                "Not allowed to enter personal data"],
            "Exam rules": ["Clear rules for exams", "Unsure what counts as cheating", "Consistent rules across faculties"],
            "Training": ["More training on prompting", "Workshops for teaching staff", "Examples of good use"],
            "Licences and costs": ["Campus licence instead of private subscriptions", "Paid versions are too expensive",
                                   "Free access for everyone"],
            "Quality": ["Answers are often wrong", "Sources are made up", "Results have to be checked"],
        },
    },
}
OTHER_LANGS = {"de": ["en", "fr", "it"], "en": ["de", "fr", "it"]}


def answers(lang: str, rng: random.Random):
    t = T[lang]
    rows = []
    themes = list(t["texts"])
    start = date(2026, 1, 1)
    for i in range(N):
        role = rng.choices(t["role"][1], weights=[60, 25, 15])[0]
        student = role == t["role"][1][0]
        age = rng.randint(18, 29) if student else rng.randint(26, 64)
        site = rng.choice(t["sites"])
        sat_w = [30, 40, 15, 5, 10] if not student else [15, 40, 25, 10, 10]
        sat = rng.choices(t["sat"][1], weights=sat_w)[0]
        nps = min(10, max(0, int(rng.gauss(8.2 if not student else 6.5, 2))))
        used = {o: (t["yes"] if rng.random() < p else t["no"])
                for o, p in zip(t["used"][1], [0.85 if student else 0.6, 0.6, 0.45 if student else 0.25, 0.2, 0.1])}
        other = rng.choice(t["other_answers"]) if used[t["used"][1][-1]] == t["yes"] else ""
        scale = t["rate"][2]
        training_w = [5, 25, 40, 25, 5] if student else [20, 45, 20, 5, 10]
        rate = [rng.choices(scale, weights=[10, 40, 35, 10, 5])[0], rng.choices(scale, weights=[15, 35, 25, 10, 15])[0],
                rng.choices(scale, weights=training_w)[0]]
        order = rng.sample(t["rank"][1], k=4)
        if student and rng.random() < 0.6:   # students put costs first
            order.remove(t["rank"][1][1])
            order.insert(0, t["rank"][1][1])
        a = rng.randint(20, 60)
        b = rng.randint(10, 100 - a - 5)
        budget = [a, b, 100 - a - b] if rng.random() > 0.05 else [a, b, 90 - a - b]   # a few do not add up
        visit = (start + timedelta(days=rng.randint(0, 150))).isoformat() if rng.random() < 0.6 else ""
        theme = rng.choice(themes[2:]) if student and rng.random() < 0.5 else rng.choice(themes)
        improve = rng.choice(t["texts"][theme]) if rng.random() < 0.8 else ""
        reasons = [rng.choice(t["texts"][rng.choice(themes)]) for _ in range(rng.choice([0, 1, 2, 2, 3]))]
        reasons += [""] * (3 - len(reasons))
        rows.append({"role": role, "faculty": rng.choice(t["faculty"][1]), "age": age, "site": site, "sat": sat,
                     "nps": nps, **{f"used_{k}": v for k, v in enumerate(used.values())}, "used_other": other,
                     **{f"rate_{k}": v for k, v in enumerate(rate)}, **{f"rank_{k}": order[k] for k in range(3)},
                     **{f"budget_{k}": v for k, v in enumerate(budget)}, "visit": visit, "improve": improve,
                     **{f"reason_{k}": v for k, v in enumerate(reasons)}, "id": i + 1})
    return rows


def config(lang: str) -> dict:
    t = T[lang]
    L = lambda text: {lang: text}  # noqa: E731
    lists = lambda items: {lang: list(items)}  # noqa: E731
    q = {}
    q["role"] = {"handler": "single_choice", "label": L(t["role"][0]), "columns": ["role"], "options": lists(t["role"][1])}
    q["faculty"] = {"handler": "single_choice", "label": L(t["faculty"][0]), "columns": ["faculty"], "options": lists(t["faculty"][1])}
    q["age"] = {"handler": "numeric", "label": L(t["age"][0]), "columns": ["age"], "numeric": {"unit": "", "decimals": 0}}
    q["satisfaction"] = {"handler": "single_choice", "label": L(t["sat"][0]), "columns": ["sat"], "scale": "satisfaction"}
    q["recommend"] = {"handler": "single_choice", "label": L(t["nps"]), "columns": ["nps"], "nps": True, "ordinal": True,
                      "options": lists([str(i) for i in range(11)])}
    q["tools_used"] = {"handler": "multi_choice_binary", "label": L(t["used"][0]),
                          "columns": [f"used_{k}" for k in range(len(t["used"][1]))], "item_labels": lists(t["used"][1]),
                          "binary_values": {"positive": t["yes"], "negative": t["no"]}}
    q["tools_used_other"] = {"handler": "freetext", "label": L(f"{t['used'][0]} – {t['used'][1][-1]}"),
                                "columns": ["used_other"], "other_of": "tools_used"}
    q["rating"] = {"handler": "matrix_likert", "label": L(t["rate"][0]), "columns": [f"rate_{k}" for k in range(3)],
                   "row_labels": lists(t["rate"][1]), "scale": "quality"}
    q["priorities"] = {"handler": "ranking", "label": L(t["rank"][0]), "columns": [f"rank_{k}" for k in range(3)],
                       "item_labels": lists(t["rank"][1])}
    q["budget"] = {"handler": "numeric_matrix", "label": L(t["budget"][0]), "columns": [f"budget_{k}" for k in range(3)],
                   "item_labels": lists(t["budget"][1]), "constant_sum": 100}
    q["last_training"] = {"handler": "date", "label": L(t["visit"]), "columns": ["visit"]}
    q["improve"] = {"handler": "freetext", "label": L(t["improve"]), "columns": ["improve"]}
    q["reasons"] = {"handler": "freetext", "label": L(t["reasons"]), "columns": [f"reason_{k}" for k in range(3)]}
    for qid, qc in q.items():
        qc["id"] = qid
        qc["type"] = qc["handler"]
    area_label, mapping = t["area"]
    age_label, buckets = t["age"][1], t["age"][2]
    settings = {
        "survey_metadata": {
            "survey_title": L(t["title"]), "survey_year": 2026, "source_language": lang,
            "languages": [lang] + OTHER_LANGS[lang],
            "about": L(t["description"]),
            # footer links of the dashboard (imprint and privacy belong to a real publisher)
            "links": {"methodology": "https://github.com/maltedreyer-5/survey-analysis-suite/blob/main/docs/methods.md"},
        },
        "llm_context": t["context"],
        # named scales (settings.scales): defined once, referenced by "scale: <name>";
        # "don't know" is no point of the scale and has no polarity
        "scales": {
            name: {"context": t["scale_context"][name], "direction": "descending",
                   "points": [{"label": L(label), **({"polarity": pol} if pol else {})}
                              for label, pol in zip(labels, ("positive", "positive", "negative", "negative", None))]}
            for name, labels in (("satisfaction", t["sat"][1]), ("quality", t["rate"][2]))
        },
        "sample_warnings": {"minimum_threshold": 5, "warning_threshold": 10},
        "segmentation": {
            "specific_clusters_min_n": 20,
            "dimensions": [
                {"id": "role", "label": L(t["role"][0].rstrip("?")), "source_question": "role",
                 "bucketing": {"type": "identity"}, "enable_specific_clusters": True},
                {"id": "area", "label": L(area_label), "source_question": "faculty",
                 "bucketing": {"type": "value_mapping", "mapping": mapping}},
                {"id": "age_group", "label": L(age_label), "source_question": "age",
                 "bucketing": {"type": "numeric_buckets",
                               "buckets": [{"label": L(name), **({"min": lo} if lo is not None else {}),
                                            **({"max": hi} if hi is not None else {})} for name, lo, hi in buckets]}},
                {"id": "site", "label": L(t["site"]), "source_column": "site", "bucketing": {"type": "identity"}},
            ],
            "cross_filters": [["role", "site"]],
        },
        "report_groups": {
            "people": {"order": 1, "label": L("Person" if lang == "de" else "About you"),
                       "questions": ["role", "faculty", "age"]},
            "services": {"order": 2, "label": L("Nutzung von KI" if lang == "de" else "Use of AI"),
                         "questions": ["satisfaction", "recommend", "tools_used", "tools_used_other", "rating",
                                       "priorities", "budget", "last_training"]},
            "open": {"order": 3, "label": L("Offene Fragen" if lang == "de" else "Open questions"),
                     "questions": ["improve", "reasons"]},
        },
    }
    return {"settings": settings, "question_groups": q}


def main():
    for lang in ("de", "en"):
        rng = random.Random(2026 if lang == "de" else 2027)
        sid = f"ai_in_higher_education_{lang}"
        d = ROOT / "backend" / "surveys" / sid
        d.mkdir(parents=True, exist_ok=True)
        (d / "survey.yaml").write_text(yaml.safe_dump({
            "id": sid, "title": {lang: T[lang]["title"]}, "description": {lang: T[lang]["description"]},
            "version": "1.0", "languages": [lang] + OTHER_LANGS[lang],
            "source": {"type": "synthetic", "generator": "examples/generate.py"}},
            allow_unicode=True, sort_keys=False), encoding="utf-8")
        header = ("# Generated by examples/generate.py – invented example survey with synthetic answers.\n"
                  "# Edit the generator, not this file.\n")
        (d / "questions.yaml").write_text(header + yaml.safe_dump(config(lang), allow_unicode=True, sort_keys=False,
                                                                     width=120), encoding="utf-8")
        rows = answers(lang, rng)
        out = ROOT / "examples" / "data" / f"{sid}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"{sid}: {len(rows)} answers -> {out.relative_to(ROOT)}")
        # the same survey as a LimeSurvey export (structure file and responses with answer codes)
        import limesurvey
        for path in limesurvey.write(T, lang, out, ROOT / "examples" / "limesurvey"):
            print(f"{sid}: LimeSurvey export -> {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
