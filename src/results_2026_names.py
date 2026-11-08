"""2026 precinct results for states placed on 2020 census VTDs by name matching (VA now; GA when
its file is in), using the same matching and pooled-vote spreading as 2022 (src/baseline_2022.py).

VA: Department of Elections "Election Results" CSV from the Enhanced Voting results site
(Download Reports), saved to data/raw/s26/va/va_results_2026_unofficial.csv. Unofficial until
the canvass (Nov 13); download again after.

Output: data/processed/s26/{st}_2026_results.csv, _centers.csv, _bg_xwalk.csv
"""

import argparse
from pathlib import Path

import pandas as pd

from baseline_2022 import build
from baseline_vest import download_blocks, read_zip_shapefile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/processed/s26"
PARTY = {"Democratic": "D", "Republican": "R"}


def load_va():
    d = pd.read_csv(ROOT / "data/raw/s26/va/va_results_2026_unofficial.csv", dtype=str, encoding="latin-1")
    d = d[d.WriteInVote != "1"]
    office = d.OfficeTitle.map(lambda t: "senate" if "United States Senate" in t else
                               ("house" if "House of Representatives" in t else None))
    d = d.assign(office=office)[office.notna()]
    return pd.DataFrame({
        "county": "51" + d.LocalityCode.str.zfill(3),
        "name": d.PrecinctName,
        "office": d.office,
        "district": pd.to_numeric(d.DistrictName.where(d.office == "house"), errors="coerce"),
        "candidate": d.CandidateName,
        "party": d.Party.map(PARTY).fillna("O"),
        "votes": pd.to_numeric(d.TOTAL_VOTES, errors="coerce").fillna(0),
    })


LOADERS = {"VA": load_va}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("states", nargs="*", default=list(LOADERS))
    for st in ap.parse_args().states:
        build(st, read_zip_shapefile(download_blocks(st)), year=2026, res=LOADERS[st](), out=OUT)


if __name__ == "__main__":
    main()
