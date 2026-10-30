"""Precinct results and geography for 2018 and 2022 from VEST, for GA, MI, TX, IA, MN, NE and VA.

VEST (Voting and Election Science Team, Harvard Dataverse) publishes one shapefile per state and
year with precinct boundaries and wide vote columns such as G18USSRSMI (General 2018, US Senate,
Republican, candidate code SMI) or GCON07DSPA (General, US House district 7, Democrat). Only
House, Senate and governor columns are kept. Special elections (S prefix) are kept and labelled;
runoffs (R prefix) are dropped.

VEST already spreads county-wide absentee and early votes into precincts in most states. Where a
file still has precincts with no geometry (county-wide reporting units), their votes are spread
across the county's real precincts in proportion to each candidate's real-precinct votes, the
same rule as src/baseline_nc.py.

Each precinct is matched to 2020 census blocks by block internal point. That gives:
  - a population-weighted precinct center (mean of block internal points, weighted by POP20)
  - a precinct x 2020 block group crosswalk with population weights

Outputs (per state and year in data/processed/vest/):
  {st}_{year}_results.csv    year, state, precinct_id, county, office, district, contest,
                             candidate_code, party, votes_real, votes_allocated
  {st}_{year}_centers.csv    precinct_id, pop20, lat, lon, center_method
  {st}_{year}_bg_xwalk.csv   precinct_id, bg_geoid, pop20, share_of_precinct, share_of_bg
"""

import argparse
import json
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/vest"
BLOCKS = ROOT / "data/raw/census_blocks"
OUT = ROOT / "data/processed/vest"

STATES = {"GA": "13", "MI": "26", "TX": "48", "IA": "19", "MN": "27", "NE": "31", "VA": "51"}
YEARS = [2018, 2022]

DATAVERSE = "https://dataverse.harvard.edu/api"
BLOCK_URL = "https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_{fips}_tabblock20.zip"

# Equal-area projection for distances and weighted centers.
CRS_AREA = "EPSG:5070"

# G18USSRSMI / S18USSDSMI / G22GOVDWHI / GCON07DSPA (House columns carry no year).
STATEWIDE = re.compile(r"^([GS])(\d\d)(USS|GOV)([A-Z])([A-Z0-9]{3})$")
HOUSE = re.compile(r"^([GS])CON(\d\d)([A-Z])([A-Z0-9]{3})$")
OFFICE = {"USS": "senate", "GOV": "governor"}
COUNTY_COLS = ["COUNTYFP", "COUNTYFP20", "COUNTYFP10", "CNTYFIPS", "COUNTY", "CNTY_NAME",
               "COUNTYNAME", "CTYNAME", "LOCALITY"]


def download_vest(st, year):
    """Find {st}_{year}.zip in the VEST dataverse and download it once."""
    name = f"{st.lower()}_{year}.zip"
    path = RAW / name
    if path.exists():
        return path
    q = urllib.parse.urlencode({"q": f'name:"{name}"', "type": "file",
                                "subtree": "electionscience", "per_page": 20})
    with urllib.request.urlopen(f"{DATAVERSE}/search?{q}") as r:
        items = json.load(r)["data"]["items"]
    hits = [i for i in items if i["name"].lower() == name]
    if not hits:
        raise FileNotFoundError(f"{name} not found in the VEST dataverse")
    RAW.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(f"{DATAVERSE}/access/datafile/{hits[0]['file_id']}", path)
    return path


def download_blocks(st):
    fips = STATES[st]
    path = BLOCKS / f"tl_2020_{fips}_tabblock20.zip"
    if not path.exists():
        BLOCKS.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(BLOCK_URL.format(fips=fips), path)
    return path


def read_zip_shapefile(path):
    with zipfile.ZipFile(path) as z:
        shp = next(n for n in z.namelist() if n.lower().endswith(".shp"))
    return gpd.read_file(f"zip://{path}!{shp}")


def vote_columns(cols, year):
    """Map vote columns to (office, district, contest, party, candidate_code)."""
    yy = f"{year % 100:02d}"
    out = {}
    for c in cols:
        if m := STATEWIDE.match(c):
            kind, y, off, party, cand = m.groups()
            if y != yy:
                continue
            office = OFFICE[off]
            contest = office + ("_special" if kind == "S" else "")
            out[c] = (office, None, contest, party, cand)
        elif m := HOUSE.match(c):
            kind, dist, party, cand = m.groups()
            contest = f"house_{int(dist):02d}" + ("_special" if kind == "S" else "")
            out[c] = ("house", int(dist), contest, party, cand)
    return out


def county_column(gdf):
    return next((c for c in COUNTY_COLS if c in gdf.columns), None)


def allocate_pooled(long, real_mask):
    """Spread votes of geometry-less units across real precincts in the same county."""
    keys = ["county", "contest", "candidate_code"]
    real = long[real_mask].copy()
    pooled = long[~real_mask].groupby(keys).votes.sum().rename("pooled")
    real_total = real.groupby(keys).votes.sum().rename("real_total")
    real = real.join(pooled, on=keys).join(real_total, on=keys)
    real["pooled"] = real["pooled"].fillna(0)
    share = (real["votes"] / real["real_total"]).where(real["real_total"] > 0, 0)
    real["votes_allocated"] = real["votes"] + real["pooled"] * share
    lost = pooled.sum() - (real["votes_allocated"] - real["votes"]).sum()
    return real.drop(columns=["pooled", "real_total"]), pooled.sum(), lost


def tidy_results(gdf, st, year):
    cols = vote_columns(gdf.columns, year)
    if not cols:
        raise ValueError(f"{st} {year}: no House/Senate/governor columns found")
    cty = county_column(gdf)
    real_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    base = pd.DataFrame({"precinct_id": gdf["precinct_id"],
                         "county": gdf[cty].astype(str) if cty else "ALL",
                         "real": real_geom})
    long = (pd.concat([base, gdf[list(cols)]], axis=1)
            .melt(id_vars=["precinct_id", "county", "real"], var_name="column", value_name="votes"))
    long["votes"] = pd.to_numeric(long["votes"], errors="coerce").fillna(0)
    meta = pd.DataFrame.from_dict(cols, orient="index",
                                  columns=["office", "district", "contest", "party", "candidate_code"])
    long = long.join(meta, on="column")

    n_pooled_units = int((~real_geom).sum())
    if n_pooled_units and not cty:
        raise ValueError(f"{st} {year}: {n_pooled_units} units without geometry and no county column")
    long, pooled, lost = allocate_pooled(long, long["real"])
    print(f"{st} {year}: {real_geom.sum():,} precincts, {len(cols)} vote columns, "
          f"{n_pooled_units} county-wide units, {pooled:,.0f} pooled votes, {lost:,.0f} unplaceable")

    long.insert(0, "state", st)
    long.insert(0, "year", year)
    long["district"] = long["district"].astype("Int64")
    long["votes_allocated"] = long["votes_allocated"].round(2)
    return long.rename(columns={"votes": "votes_real"})[
        ["year", "state", "precinct_id", "county", "office", "district", "contest",
         "candidate_code", "party", "votes_real", "votes_allocated"]
    ]


def match_blocks(precincts, blocks):
    """Assign each 2020 block to the precinct containing its internal point."""
    pts = gpd.GeoDataFrame(
        {"block_geoid": blocks["GEOID20"], "pop20": blocks["POP20"].astype(int)},
        geometry=gpd.points_from_xy(blocks["INTPTLON20"].astype(float),
                                    blocks["INTPTLAT20"].astype(float)),
        crs="EPSG:4326",
    ).to_crs(CRS_AREA)
    polys = precincts[["precinct_id", "geometry"]].to_crs(CRS_AREA)
    hit = gpd.sjoin(pts, polys, how="left", predicate="within")
    # A point on a shared edge can match two precincts; keep the first.
    hit = hit[~hit.index.duplicated(keep="first")]
    unmatched = hit["precinct_id"].isna()
    if unmatched.any():
        # Blocks outside every precinct (gaps, water, edge slivers) go to the nearest precinct.
        near = gpd.sjoin_nearest(pts[unmatched], polys, how="left")
        near = near[~near.index.duplicated(keep="first")]
        hit.loc[unmatched, "precinct_id"] = near["precinct_id"]
    print(f"  blocks: {len(hit):,}, {unmatched.sum():,} matched by nearest precinct "
          f"({hit.loc[unmatched, 'pop20'].sum():,} people)")
    return hit


def centers(precincts, hit):
    hit = hit.assign(x=hit.geometry.x, y=hit.geometry.y)
    w = hit[hit.pop20 > 0]
    agg = w.assign(wx=w.x * w.pop20, wy=w.y * w.pop20).groupby("precinct_id")[["wx", "wy", "pop20"]].sum()
    c = pd.DataFrame({"x": agg.wx / agg.pop20, "y": agg.wy / agg.pop20, "pop20": agg.pop20})
    c["center_method"] = "pop_weighted"

    # Precincts with no 2020 residents fall back to a point inside the polygon.
    polys = precincts.set_index("precinct_id").to_crs(CRS_AREA)
    empty = polys.index.difference(c.index)
    rep = polys.loc[empty].geometry.representative_point()
    c = pd.concat([c, pd.DataFrame({"x": rep.x, "y": rep.y, "pop20": 0,
                                    "center_method": "polygon_point"})])
    ll = gpd.GeoSeries(gpd.points_from_xy(c.x, c.y), crs=CRS_AREA, index=c.index).to_crs("EPSG:4326")
    c["lat"], c["lon"] = ll.y.round(6), ll.x.round(6)
    print(f"  centers: {len(c):,} precincts, {len(empty):,} with no 2020 population")
    return c.rename_axis("precinct_id").reset_index()[["precinct_id", "pop20", "lat", "lon", "center_method"]]


def bg_crosswalk(hit):
    x = hit.assign(bg_geoid=hit.block_geoid.str[:12]).groupby(["precinct_id", "bg_geoid"]).pop20.sum()
    x = x.reset_index()
    x["share_of_precinct"] = x.pop20 / x.groupby("precinct_id").pop20.transform("sum")
    x["share_of_bg"] = x.pop20 / x.groupby("bg_geoid").pop20.transform("sum")
    return x.fillna({"share_of_precinct": 0, "share_of_bg": 0}).round({"share_of_precinct": 6,
                                                                       "share_of_bg": 6})


def build(st, year, blocks):
    gdf = read_zip_shapefile(download_vest(st, year))
    gdf["precinct_id"] = [f"{st}{year % 100:02d}_{i:05d}" for i in range(len(gdf))]
    results = tidy_results(gdf, st, year)

    precincts = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    hit = match_blocks(precincts, blocks)

    OUT.mkdir(parents=True, exist_ok=True)
    keep = [c for c in gdf.columns if c not in vote_columns(gdf.columns, year)]
    gdf[keep].to_file(OUT / f"{st.lower()}_{year}_precincts.gpkg", driver="GPKG")
    results.to_csv(OUT / f"{st.lower()}_{year}_results.csv", index=False)
    centers(precincts, hit).to_csv(OUT / f"{st.lower()}_{year}_centers.csv", index=False)
    bg_crosswalk(hit).to_csv(OUT / f"{st.lower()}_{year}_bg_xwalk.csv", index=False)
    print(results.groupby("office").votes_allocated.sum().round(0).to_string())


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--states", nargs="+", default=list(STATES))
    p.add_argument("--years", nargs="+", type=int, default=YEARS)
    a = p.parse_args()
    for st in a.states:
        blocks = read_zip_shapefile(download_blocks(st))
        for year in a.years:
            build(st, year, blocks)


if __name__ == "__main__":
    main()
