# -*- coding: utf-8 -*-
"""Judge the pre-registered work/transit rule on a batch of labels.

THE RULE, FROZEN 2026-08-15 BEFORE ANY NEW LABELS EXISTED

    site is WORK  <=>  area >= 1.3 ha  OR  time on site >= 25 min

It was found on 66 labelled sites (leave-one-out 92% against a 55% majority
baseline) and it is NOT adopted. The thresholds were fitted honestly, but the
SHAPE of the rule -- first "and", then a product, then "or" -- was searched on
the same data, and cross-validation does not cover that search. So the rule
waits for labels it has never seen, and this script is the referee.

ACCEPTANCE, ALSO DECLARED IN ADVANCE (roadmap section 2.8.1)
  1. at least 40 sites in the batch, at least 15 of them transits;
  2. accuracy not below 85 percent;
  3. transits that would go into the bill: not more than 10 percent of the
     batch's transits.
Fail any of the three and the rule is rejected whole -- not re-tuned. Moving a
threshold after seeing the answer turns a test back into a fit.

Run (PowerShell, one command per line):

  cd C:\\diag\\wialon
  & C:\\gps_venv\\Scripts\\python.exe C:\\transport-report\\tools\\gps_label_evaluate.py --answers otvety.txt --features gps_label.csv

Without arguments it reports on the corpus in gps/data/labelled_sites.json --
those are TRAINING numbers and the script says so.

Console output is ASCII apart from the two Russian labels it reads from data.
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(HERE, os.pardir, "gps", "data", "labelled_sites.json")

# Frozen 2026-08-15. Do not touch to make a batch pass: that is the one move
# this whole protocol exists to prevent.
RULE_AREA_HA = 1.3
RULE_MINUTES = 25.0

ACCEPT_MIN_SITES = 40
ACCEPT_MIN_TRANSITS = 15
ACCEPT_MIN_ACCURACY = 0.85
ACCEPT_MAX_EXPENSIVE_SHARE = 0.10

WORK, PASSAGE = "работа", "проезд"


def says_work(row):
    return row["area_ha"] >= RULE_AREA_HA or row["minutes"] >= RULE_MINUTES


def read_answers(path):
    """Lines produced by the labelling page: <site_id>;<label>"""
    answers = {}
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line or ";" not in line:
                continue
            site_id, label = line.rsplit(";", 1)
            if label in (WORK, PASSAGE):
                answers[site_id] = label
    return answers


def read_features(path):
    rows = {}
    with open(path, encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh, delimiter=";"):
            try:
                rows[row["site_id"]] = {
                    "site_id": row["site_id"], "machine": row["machine"],
                    "day": row["day"], "area_ha": float(row["area_ha"]),
                    "minutes": float(row["minutes"])}
            except (KeyError, ValueError):
                continue
    return rows


def judge(rows, unseen):
    kind = "UNSEEN BATCH" if unseen else "TRAINING CORPUS (not a test)"
    print("== %s: %d sites, %s ==" % (kind, len(rows), Counter(r["label"] for r in rows)))
    transits = [r for r in rows if r["label"] == PASSAGE]
    correct = [r for r in rows if (says_work(r)) == (r["label"] == WORK)]
    into_bill = [r for r in rows if r["label"] == PASSAGE and says_work(r)]
    lost = [r for r in rows if r["label"] == WORK and not says_work(r)]
    accuracy = len(correct) / len(rows) if rows else 0.0
    majority = (Counter(r["label"] for r in rows).most_common(1)[0][1] / len(rows)
                if rows else 0.0)

    print("rule: area >= %.2f ha OR time >= %.1f min" % (RULE_AREA_HA, RULE_MINUTES))
    print("accuracy %.0f%%   (majority baseline %.0f%%)"
          % (accuracy * 100, majority * 100))
    print("transits that would be billed: %d of %d" % (len(into_bill), len(transits)))
    print("works that would be dropped:   %d" % len(lost))
    for r in into_bill:
        print("   BILLED  %-24s %s %6.2f ha %7.1f min"
              % (r["machine"][:24], r["day"], r["area_ha"], r["minutes"]))
    for r in lost:
        print("   DROPPED %-24s %s %6.2f ha %7.1f min"
              % (r["machine"][:24], r["day"], r["area_ha"], r["minutes"]))

    if not unseen:
        print("\nThese are the numbers the rule was built on. They prove nothing;"
              " run this on a batch the rule has never seen.")
        return 0

    share = len(into_bill) / len(transits) if transits else 1.0
    checks = [("batch large enough", len(rows) >= ACCEPT_MIN_SITES,
               "%d of %d" % (len(rows), ACCEPT_MIN_SITES)),
              ("enough transits", len(transits) >= ACCEPT_MIN_TRANSITS,
               "%d of %d" % (len(transits), ACCEPT_MIN_TRANSITS)),
              ("accuracy", accuracy >= ACCEPT_MIN_ACCURACY,
               "%.0f%% of %.0f%%" % (accuracy * 100, ACCEPT_MIN_ACCURACY * 100)),
              ("billed transits", share <= ACCEPT_MAX_EXPENSIVE_SHARE,
               "%.0f%% of %.0f%%" % (share * 100, ACCEPT_MAX_EXPENSIVE_SHARE * 100))]
    print()
    for name, passed, detail in checks:
        print("%-4s %-22s %s" % ("PASS" if passed else "FAIL", name, detail))
    verdict = all(passed for _, passed, _ in checks)
    print("\nVERDICT: rule %s" % ("ACCEPTED -- may enter the module as a "
                                 "pre-filled answer" if verdict
                                 else "REJECTED -- and NOT re-tuned"))
    return 0 if verdict else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--answers", help="text from the labelling page")
    parser.add_argument("--features", help="gps_label.csv built with that page")
    args = parser.parse_args()

    if bool(args.answers) != bool(args.features):
        sys.stderr.write("ERROR: --answers and --features go together\n")
        return 2

    if args.answers:
        answers = read_answers(args.answers)
        features = read_features(args.features)
        rows, orphans = [], 0
        for site_id, label in answers.items():
            if site_id not in features:
                orphans += 1
                continue
            row = dict(features[site_id])
            row["label"] = label
            rows.append(row)
        if orphans:
            print("WARNING: %d answers have no matching site in %s -- was the "
                  "csv built by the same run as the page?" % (orphans, args.features))
        if not rows:
            sys.stderr.write("ERROR: nothing to judge\n")
            return 2
        # [REASON]: a batch that repeats sites the rule was built on is not a
        # test of anything. Anything already in the corpus is dropped and said
        # aloud, rather than quietly inflating the score.
        known = {r["site_id"] for r in json.load(open(CORPUS, encoding="utf-8"))}
        fresh = [r for r in rows if r["site_id"] not in known]
        if len(fresh) != len(rows):
            print("note: %d of %d answers are sites already in the corpus and "
                  "are excluded from the test" % (len(rows) - len(fresh), len(rows)))
        return judge(fresh, unseen=True)

    return judge(json.load(open(CORPUS, encoding="utf-8")), unseen=False)


if __name__ == "__main__":
    sys.exit(main())
