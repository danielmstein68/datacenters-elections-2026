"""Incumbent party for each 2026 governor, U.S. Senate and U.S. House race in the study states.

House and Senate seat holders come from the unitedstates/congress-legislators project
(legislators-current.yaml, downloaded 2026-11-07 to data/raw/legislators/). Governors and
whether a statewide incumbent is on the 2026 ballot are entered by hand below, with sources.
Whether each House incumbent is on the ballot is checked against the 2026 candidate names
once results are in (incumbent_on_ballot = "check").

Output: data/hand/incumbents_2026.csv
"""

from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data/raw/legislators/legislators-current_2026-11-07.yaml"
OUT = ROOT / "data/hand/incumbents_2026.csv"
STATES = ["GA", "MI", "TX", "MN", "NC", "VA", "AZ", "IN", "WI"]
PARTY = {"Democrat": "D", "Republican": "R"}

# Statewide 2026 races in the study states. on_ballot: is the seat holder a 2026 candidate?
GOVERNOR = [
    ("GA", "Brian Kemp", "R", False, "term-limited"),
    ("MI", "Gretchen Whitmer", "D", False, "term-limited"),
    ("TX", "Greg Abbott", "R", True, "running for a fourth term"),
    ("MN", "Tim Walz", "D", False, "withdrew Jan 5, 2026 (KAXE, 2026-01-05)"),
    ("AZ", "Katie Hobbs", "D", True, "running for a second term"),
    ("WI", "Tony Evers", "D", False, "announced July 2025 he would not run"),
]
SENATE = {  # state: (on_ballot, note); seat holder comes from the legislators file (class 2 seat)
    "GA": (True, "Ossoff running"),
    "MI": (False, "Peters retiring"),
    "TX": (False, "Cornyn lost the May 26, 2026 GOP runoff to Paxton"),
    "MN": (False, "Smith retiring"),
    "NC": (False, "Tillis retiring"),
    "VA": (True, "Warner running"),
}


def main():
    leg = yaml.safe_load(open(SRC))
    rows = []
    for p in leg:
        t = p["terms"][-1]
        if t["state"] not in STATES:
            continue
        name = p["name"].get("official_full") or f"{p['name']['first']} {p['name']['last']}"
        party = PARTY.get(t["party"], "O")
        if t["type"] == "rep":
            rows.append({"state": t["state"], "office": "house", "district": t["district"], "incumbent": name,
                         "incumbent_party": party, "incumbent_on_ballot": "check", "note": ""})
        elif t["type"] == "sen" and t.get("class") == 2 and t["state"] in SENATE:
            on, note = SENATE[t["state"]]
            rows.append({"state": t["state"], "office": "senate", "district": None, "incumbent": name,
                         "incumbent_party": party, "incumbent_on_ballot": on, "note": note})
    for st, name, party, on, note in GOVERNOR:
        rows.append({"state": st, "office": "governor", "district": None, "incumbent": name,
                     "incumbent_party": party, "incumbent_on_ballot": on, "note": note})
    df = pd.DataFrame(rows).sort_values(["state", "office", "district"])
    df["district"] = df.district.astype("Int64")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False)
    print(df[df.office != "house"].to_string(index=False))
    print(df[df.office == "house"].groupby("state").size().to_dict())


if __name__ == "__main__":
    main()
