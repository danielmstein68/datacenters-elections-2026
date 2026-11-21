"""Minnesota 2026 precinct results on the LCC 2024 general-election precinct map.

Results: Secretary of State allracesbyprecinct.txt for 2026-11-03 (unofficial until the state
canvass; re-download after Nov 11). Rows are keyed by the state's county code + precinct code,
which the LCC map carries (COUNTYCODE, PCTCODE). Precincts in the results but not on the 2024
map have their votes spread over the county's matched precincts, as elsewhere. Switch to the
LCC 2026 map when it is posted.

Outputs: data/processed/s26/mn_2026_results.csv, _centers.csv, _bg_xwalk.csv
"""

import re
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from baseline_vest import bg_crosswalk, centers, download_blocks, match_blocks, read_zip_shapefile

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/s26/mn"
OUT = ROOT / "data/processed/s26"
RES_URL = "https://electionresultsfiles.sos.mn.gov/20261103/allracesbyprecinct.txt"
MAP_URL = "https://gis.lcc.mn.gov/data/shape/vtd2024general.zip"
COLS = ["state", "county_code", "pct_code", "office_id", "office_name", "district", "cand_code", "candidate",
        "suffix", "incumbent", "party", "pcts_reporting", "pcts_total", "votes", "pct", "office_total"]
PARTY = {"DFL": "D", "R": "R"}


def fetch(url, path):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, path)
    return path


def office(name):
    if name == "U.S. Senator":
        return "senate", None
    if name.startswith("Governor"):
        return "governor", None
    m = re.fullmatch(r"U.S. Representative District (\d+)", name)
    return ("house", int(m.group(1))) if m else (None, None)


def main():
    r = pd.read_csv(fetch(RES_URL, RAW / "allracesbyprecinct_20261103.txt"), sep=";", header=None, names=COLS,
                    dtype=str, encoding="latin-1")
    r["office"], r["district"] = zip(*r.office_name.map(office))
    r = r[r.office.notna()].copy()
    r["votes"] = pd.to_numeric(r.votes, errors="coerce").fillna(0)
    r["key"] = r.county_code.str.zfill(2) + r.pct_code.str.zfill(4)

    g = read_zip_shapefile(fetch(MAP_URL, RAW / "vtd2024general.zip"))
    g["key"] = g.COUNTYCODE.astype(str).str.zfill(2) + g.PCTCODE.astype(str).str.zfill(4)
    g["precinct_id"] = "MN24_" + g.VTDID.astype(str)
    g["county"] = "27" + g.COUNTYFIPS.astype(str).str.zfill(3)
    keymap = g.drop_duplicates("key").set_index("key")
    r["precinct_id"], r["county"] = r.key.map(keymap.precinct_id), r.key.map(keymap.county)
    cty = g.drop_duplicates("COUNTYCODE").set_index(g.drop_duplicates("COUNTYCODE").COUNTYCODE.astype(str).str.zfill(2)).county
    r["county"] = r.county.fillna(r.county_code.str.zfill(2).map(cty))

    hit = r.precinct_id.notna()
    keys = ["county", "office", "district", "candidate"]
    m = r[hit].copy()
    extra = r[~hit].groupby(keys, dropna=False).votes.sum().rename("extra")
    base = m.groupby(keys, dropna=False).votes.sum().rename("base")
    m = m.join(extra, on=keys).join(base, on=keys)
    m["votes_allocated"] = (m.votes + m.extra.fillna(0) * (m.votes / m.base).where(m.base > 0, 0)).round(2)
    print(f"results precincts: {r.key.nunique():,}, on map: {r[hit].key.nunique():,}; "
          f"votes in matched precincts: {m.votes.sum() / r.votes.sum():.4f}")
    out = pd.DataFrame({"year": 2026, "state": "MN", "precinct_id": m.precinct_id, "county": m.county,
                        "office": m.office, "district": m.district, "contest": m.office_name,
                        "candidate_code": m.candidate, "party": m.party.map(PARTY).fillna("O"),
                        "votes_real": m.votes, "votes_allocated": m.votes_allocated})
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT / "mn_2026_results.csv", index=False)

    blocks = read_zip_shapefile(download_blocks("MN"))
    hitb = match_blocks(g, blocks)
    centers(g, hitb).to_csv(OUT / "mn_2026_centers.csv", index=False)
    bg_crosswalk(hitb).to_csv(OUT / "mn_2026_bg_xwalk.csv", index=False)


if __name__ == "__main__":
    main()
