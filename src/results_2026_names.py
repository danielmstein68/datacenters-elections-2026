"""2026 precinct results for states placed on 2020 census VTDs by name matching (VA, GA), using the same matching and pooled-vote spreading as 2022 (src/baseline_2022.py).

VA: Department of Elections "Election Results" CSV from the Enhanced Voting results site
(Download Reports), saved to data/raw/s26/va/va_results_2026_unofficial.csv. Unofficial until
the canvass (Nov 13); download again after.

GA: the Secretary of State's results site (Enhanced Voting) publishes one statewide media export
with precinct results for every county:
https://results.sos.ga.gov/cdn/results/Georgia/export-11032026GeneralAndSpecialElection.json
saved to data/raw/s26/ga/export_state.json. County names get FIPS codes from the Census 2020
county list (data/raw/census/national_county2020.txt). Unofficial until the canvass; download again after.

Output: data/processed/s26/{st}_2026_results.csv, _centers.csv, _bg_xwalk.csv
"""

import argparse
import json
import re
import urllib.request
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


GA_EXPORT = ("https://results.sos.ga.gov/cdn/results/Georgia/"
             "export-11032026GeneralAndSpecialElection.json")
COUNTY_LIST = "https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt"


def county_fips(st):
    path = ROOT / "data/raw/census/national_county2020.txt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(COUNTY_LIST, path)
    c = pd.read_csv(path, sep="|", dtype=str)
    c = c[c.STATE == st]
    return dict(zip(c.COUNTYNAME.str.upper(), c.STATEFP + c.COUNTYFP))


def load_ga():
    path = ROOT / "data/raw/s26/ga/export_state.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(GA_EXPORT, path)
    fips = county_fips("GA")
    rows = []
    for c in json.load(open(path))["localResults"]:
        cty = fips[c["name"].upper()]
        for b in c["ballotItems"]:
            name = b["name"].split("/")[0].strip()
            if name == "US Senate":
                office, dist = "senate", None
            elif name == "Governor":
                office, dist = "governor", None
            elif name.startswith("US House of Representatives"):
                office, dist = "house", int(re.search(r"District (\d+)", name).group(1))
            else:
                continue
            for o in b["ballotOptions"]:
                tag = (o.get("politicalParty") or "").upper() or \
                    (re.search(r"\((\w+)\)\s*$", o["name"]) or [None, ""])[1].upper()
                party = {"REP": "R", "DEM": "D"}.get(tag, "O")
                for p in o["precinctResults"]:
                    rows.append((cty, p["name"], office, dist, o["name"], party, p["voteCount"]))
    return pd.DataFrame(rows, columns=["county", "name", "office", "district", "candidate", "party", "votes"])


LOADERS = {"VA": load_va, "GA": load_ga}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("states", nargs="*", default=list(LOADERS))
    for st in ap.parse_args().states:
        build(st, read_zip_shapefile(download_blocks(st)), year=2026, res=LOADERS[st](), out=OUT)


if __name__ == "__main__":
    main()
