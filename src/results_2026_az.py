"""Arizona 2026 precinct results (Maricopa County now; Pima and the rest after their canvasses).

Maricopa: the county's "Arizona Export By Precinct" tab-delimited file from
https://elections.maricopa.gov/results-and-data/election-results.html, saved to
data/raw/s26/az/results/. Its PrecinctExtId is the precinct number on Maricopa's 2026 precinct map
(src/maps_2026.py, ids AZ26MC_<number>), so results join to the map exactly. Unofficial until the
county canvass; download the newest export again after.

Output: data/processed/s26/az_2026_results.csv (same columns as the 2022 results files).
Centers and the block-group crosswalk come from src/maps_2026.py.
"""

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/s26/az/results"
OUT = ROOT / "data/processed/s26"
PARTY = {"REP": "R", "DEM": "D"}


def office_of(contest):
    if contest == "Governor":
        return "governor", None
    if contest.startswith("US Rep Dist CD-"):
        return "house", int(contest.rsplit("-", 1)[1])
    return None, None


def load_maricopa(path):
    d = pd.read_csv(path, sep="\t", dtype=str)
    d = d[d.CandidateType != "W"]  # write-in lines
    oc = d.ContestName.map(office_of)
    d = d.assign(office=oc.str[0], district=oc.str[1])
    d = d[d.office.notna()]
    names = pd.read_csv(OUT / "az_2026_names.csv", dtype=str)
    pid = "AZ26MC_" + d.PrecinctExtId.str.zfill(4)
    out = pd.DataFrame({
        "year": 2026, "state": "AZ", "precinct_id": pid, "county": "04013",
        "office": d.office, "district": d.district, "contest": d.ContestName,
        "candidate_code": d.CandidateName, "party": d.CandidateAffiliation.map(PARTY).fillna("O"),
        "votes_real": pd.to_numeric(d.Votes, errors="coerce").fillna(0),
    })
    out["votes_allocated"] = out.votes_real
    miss = ~out.precinct_id.isin(names.precinct_id)
    print(f"Maricopa: {out.precinct_id.nunique():,} precincts, "
          f"{out.votes_real[miss].sum() / out.votes_real.sum():.2%} of votes not on the 2026 map")
    return out[~miss]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maricopa", default=None, help="export file (default: newest in data/raw/s26/az/results)")
    a = ap.parse_args()
    path = Path(a.maricopa) if a.maricopa else sorted(RAW.glob("maricopa_export_*.txt"))[-1]
    res = load_maricopa(path)
    res.to_csv(OUT / "az_2026_results.csv", index=False)
    print(res.groupby("office").precinct_id.nunique().to_string())


if __name__ == "__main__":
    main()
