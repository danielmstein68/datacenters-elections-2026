"""Put North Carolina's 2018 and 2022 precinct results on the State Board's precinct maps.

Maps: NCSBE statewide precinct shapefiles (s3 dl.ncsbe.gov/ShapeFiles/Precinct/), the last
release before each election: SBE_PRECINCTS_20170519 for 2018 (no release between May 2017
and May 2019) and SBE_PRECINCTS_20220831 for 2022. Results (data/processed/nc_precinct_results.csv,
from src/baseline_nc.py) are keyed by county and precinct code; the maps carry the same codes
(PREC_ID), so they are joined on county name + code.

Results precincts with no polygon on the map are reported. Their votes are spread over the
county's matched precincts in proportion to each candidate's votes, the same way pooled
absentee votes are handled.

Centers and block-group crosswalks are built exactly as in src/baseline_vest.py (2020 blocks
by internal point, population-weighted centers).

Outputs: data/processed/nc/nc_{year}_results.csv, _centers.csv, _bg_xwalk.csv, match_report.csv
"""

import re
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from baseline_vest import BLOCK_URL, BLOCKS, bg_crosswalk, centers, match_blocks

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/nc"
OUT = ROOT / "data/processed/nc"
MAPS = {2018: "20170519", 2022: "20220831"}
URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_{d}.zip"


def norm(code):
    s = str(code).strip().upper()
    s = re.sub(r"\s+", "", s)
    return s.lstrip("0") or "0"


def load_map(year):
    path = RAW / f"SBE_PRECINCTS_{MAPS[year]}.zip"
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL.format(d=MAPS[year]), path)
    g = gpd.read_file(f"zip://{path}")
    g.columns = [c.lower() if c != "geometry" else c for c in g.columns]
    g["county"] = g.county_nam.str.strip().str.upper()
    g["key"] = g.prec_id.map(norm)
    # A few precincts are drawn as several polygons; merge them.
    g = g.dissolve(by=["county", "key"], as_index=False, aggfunc="first")
    g["precinct_id"] = [f"NC{year % 100:02d}_{c[:4]}_{k}" for c, k in zip(g.county.str.replace(" ", ""), g.key)]
    return g[["precinct_id", "county", "key", "prec_id", "enr_desc", "geometry"]]


def place_results(res, g, year):
    r = res[res.year == year].copy()
    r["county"] = r.county.str.strip().str.upper()
    r["key"] = r.precinct.map(norm)
    r = r.merge(g[["county", "key", "precinct_id"]], on=["county", "key"], how="left")
    hit = r.precinct_id.notna()
    tot = r.groupby("office").votes_allocated.sum()
    unmatched = r[~hit].groupby(["county", "precinct"]).votes_allocated.sum().sort_values(ascending=False)

    keys = ["county", "contest", "candidate"]
    m = r[hit].copy()
    extra = r[~hit].groupby(keys).votes_allocated.sum().rename("extra")
    base = m.groupby(keys).votes_allocated.sum().rename("base")
    m = m.join(extra, on=keys).join(base, on=keys)
    share = (m.votes_allocated / m.base).where(m.base > 0, 0)
    m["votes_allocated"] = (m.votes_allocated + m.extra.fillna(0) * share).round(2)
    placed = m.groupby("office").votes_allocated.sum() / tot
    report = pd.DataFrame({
        "year": year,
        "office": tot.index,
        "votes": tot.values.round(0),
        "share_in_matched_precincts": (r[hit].groupby("office").votes_allocated.sum() / tot).reindex(tot.index).round(4).values,
        "share_placed_after_spreading": placed.reindex(tot.index).round(4).values,
    })
    print(f"{year}: {m[['county', 'key']].drop_duplicates().shape[0]} "
          f"results precincts on the map, {len(unmatched)} not; largest unmatched:\n"
          f"{unmatched.head(8).round(0).to_string()}")
    m["state"] = "NC"
    m = m.drop(columns=["extra", "base", "key"])
    return m[["year", "state", "precinct_id", "county", "office", "district", "contest", "candidate", "party",
              "votes_real", "votes_allocated"]], report


def main():
    res = pd.read_csv(ROOT / "data/processed/nc_precinct_results.csv")
    res["party"] = res.party.map({"REP": "R", "DEM": "D"}).fillna("O")
    bpath = BLOCKS / "tl_2020_37_tabblock20.zip"
    if not bpath.exists():
        BLOCKS.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BLOCK_URL.format(fips="37"), bpath)
    blocks = gpd.read_file(f"zip://{bpath}", columns=["GEOID20", "POP20", "INTPTLAT20", "INTPTLON20"],
                           ignore_geometry=True)
    OUT.mkdir(parents=True, exist_ok=True)
    reports = []
    for year in MAPS:
        g = load_map(year)
        results, rep = place_results(res, g, year)
        reports.append(rep)
        hit = match_blocks(g, blocks)
        results.to_csv(OUT / f"nc_{year}_results.csv", index=False)
        centers(g, hit).to_csv(OUT / f"nc_{year}_centers.csv", index=False)
        bg_crosswalk(hit).to_csv(OUT / f"nc_{year}_bg_xwalk.csv", index=False)
    rep = pd.concat(reports)
    rep.to_csv(OUT / "match_report.csv", index=False)
    print(rep.to_string(index=False))


if __name__ == "__main__":
    main()
