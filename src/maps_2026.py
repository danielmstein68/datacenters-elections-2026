"""Precinct centers and block-group crosswalks for 2026 precinct maps (no results needed).

Built now for the states whose 2026 maps are posted (data/hand/maps_2026.csv):
  NC  NCSBE statewide precincts, 2026-08-24
  IN  IndianaMap Voting District Boundaries 2026
  AZ  Maricopa County Voting Precincts (2026-); the rest of AZ waits for results
  WI  LTSB current municipal wards
Each map gets precinct ids, a name table for matching 2026 results to it, population-weighted
centers and a precinct -> block group crosswalk, made exactly as for 2018/2022.

Outputs: data/processed/s26/{st}_2026_centers.csv, _bg_xwalk.csv, _names.csv
         (NC: data/processed/nc/nc_2026_*.csv, keyed on county + precinct code as in 2022)
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from baseline_2022 import arcgis_layer
from baseline_nc_geo import norm
from baseline_vest import bg_crosswalk, centers, download_blocks, match_blocks, read_zip_shapefile, STATES as FIPS

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/s26"
OUT = ROOT / "data/processed/s26"
NC_MAP = "https://s3.amazonaws.com/dl.ncsbe.gov/ShapeFiles/Precinct/SBE_PRECINCTS_20260824.zip"
IN_MAP = "https://gisdata.in.gov/server/rest/services/Hosted/Voting_District_Boundaries_2026/FeatureServer/0/query"
MARICOPA_MAP = ("https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/"
                "Maricopa_County_Voting_Precincts_(2026_)/FeatureServer/0/query")
WI_MAP = ("https://services1.arcgis.com/FDsAtKBk8Hy4cAH0/arcgis/rest/services/"
          "WI_Municipal_Wards_Current/FeatureServer/0/query")
FIPS = {**FIPS, "NC": "37"}


def first_col(df, *names):
    for n in names:
        for c in df.columns:
            if c.lower() == n.lower():
                return df[c]
    raise KeyError(f"none of {names} in {list(df.columns)}")


def load_nc():
    path = RAW / "nc/SBE_PRECINCTS_20260824.zip"
    if not path.exists():
        import urllib.request
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(NC_MAP, path)
    g = gpd.read_file(f"zip://{path}")
    g.columns = [c.lower() if c != "geometry" else c for c in g.columns]
    g["county"] = g.county_nam.str.strip().str.upper()
    g["key"] = g.prec_id.map(norm)
    g = g.dissolve(by=["county", "key"], as_index=False, aggfunc="first")
    g["precinct_id"] = [f"NC26_{c[:4]}_{k}" for c, k in zip(g.county.str.replace(" ", ""), g.key)]
    g["name"] = g.enr_desc
    return g[["precinct_id", "county", "key", "name", "geometry"]]


def load_in():
    v = arcgis_layer(IN_MAP, RAW / "in/in_precincts_2026.geojson")
    v["precinct_id"] = "IN26_" + first_col(v, "p26_stfid", "stfid", "precinct_id", "objectid").astype(str)
    v["county"] = "18" + first_col(v, "county", "county_fips", "cnty_fips").astype(str).str[-3:].str.zfill(3)
    v["name"] = first_col(v, "p26", "precinct", "name", "prec_name").astype(str)
    return v[["precinct_id", "county", "name", "geometry"]]


def load_az():
    m = arcgis_layer(MARICOPA_MAP, RAW / "az/maricopa_precincts_2026.geojson")
    code = first_col(m, "BdVal", "PctNum", "PRECINCT")
    m["precinct_id"] = "AZ26MC_" + code.astype(str)
    m["county"] = "04013"
    m["name"] = code.astype(str).str.zfill(4) + " " + first_col(m, "BdName", "PctName", "NAME").astype(str)
    return m[["precinct_id", "county", "name", "geometry"]]


def load_wi():
    v = arcgis_layer(WI_MAP, RAW / "wi/wi_wards_current.geojson", page=2000)
    v["precinct_id"] = "WI26_" + first_col(v, "GEOID", "WARD_FIPS", "OBJECTID").astype(str)
    v["county"] = "55" + first_col(v, "CNTY_FIPS", "COUNTY_FIPS", "CNTYFIPS").astype(str).str[-3:].str.zfill(3)
    v["name"] = first_col(v, "LABEL", "MCD_NAME", "NAME").astype(str)
    return v[["precinct_id", "county", "name", "geometry"]]


LOADERS = {"NC": load_nc, "IN": load_in, "AZ": load_az, "WI": load_wi}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("states", nargs="*", default=list(LOADERS))
    args = ap.parse_args()
    for st in args.states:
        print(f"== {st}")
        g = LOADERS[st]()
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        g = g.dissolve(by="precinct_id", as_index=False, aggfunc="first") if g.precinct_id.duplicated().any() else g
        blocks = read_zip_shapefile(download_blocks(st)) if st != "NC" else gpd.read_file(
            f"zip://{ROOT / 'data/raw/census_blocks/tl_2020_37_tabblock20.zip'}")
        if st == "AZ":  # only Maricopa's map is posted
            blocks = blocks[blocks.GEOID20.str[:5] == "04013"]
        hit = match_blocks(g, blocks)
        out = ROOT / "data/processed/nc" if st == "NC" else OUT
        out.mkdir(parents=True, exist_ok=True)
        pre = out / f"{st.lower()}_2026"
        g.drop(columns="geometry").to_csv(f"{pre}_names.csv", index=False)
        centers(g, hit).to_csv(f"{pre}_centers.csv", index=False)
        bg_crosswalk(hit).to_csv(f"{pre}_bg_xwalk.csv", index=False)
        print(f"  {len(g):,} precincts")


if __name__ == "__main__":
    main()
