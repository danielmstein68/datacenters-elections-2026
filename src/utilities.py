"""Electric utility service areas for the utility-bill test.

Source: HIFLD Electric Retail Service Territories (EIA-861 based), served from PNNL's mirror
(eedgis.pnnl.gov ... Hosted/Electric_Service_Territories/FeatureServer/0), since the HIFLD Open
portal closed in 2025. Polygons are downloaded for the study states and neighbors, with light
simplification (about 100 m), and cached in data/raw/utility/.

Territories overlap (co-ops, municipal utilities and investor-owned utilities can all cover a
point), so each point gets every territory that contains it.

Outputs:
  data/processed/utility/site_utilities.csv     site_id -> utility ids (treated_main sites)
  data/processed/utility/{st}_2022_utilities.csv  precinct_id -> utility ids, and flags:
      shares_utility_any / _wave: precinct center lies in a territory that also contains a
      large site (any vintage / 2023+ wave), anywhere in that territory
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/utility"
OUT = ROOT / "data/processed/utility"
URL = "https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0/query"
STATES = ["GA", "MI", "TX", "MN", "NC", "VA", "AZ", "IN", "WI"]
NEIGHBORS = ["AL", "FL", "SC", "TN", "OH", "LA", "OK", "NM", "AR", "IA", "ND", "SD", "KY", "WV", "MD", "DC",
             "CA", "NV", "UT", "IL"]
KEEP = ["id", "name", "state", "type", "holding_co", "customers", "year", "sourcedate"]


def fetch_all():
    path = RAW / "retail_service_territories.gpkg"
    if path.exists():
        return gpd.read_file(path)
    RAW.mkdir(parents=True, exist_ok=True)
    ids_q = {"where": "1=1", "returnIdsOnly": "true", "f": "json"}
    ids = json.load(urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(ids_q), timeout=120))["objectIds"]
    parts = []
    for i in range(0, len(ids), 50):
        q = {"objectIds": ",".join(map(str, ids[i:i + 50])), "outFields": ",".join(KEEP), "outSR": "4326",
             "maxAllowableOffset": "0.001", "f": "geojson"}
        for attempt in range(4):
            try:
                body = urllib.request.urlopen(URL, data=urllib.parse.urlencode(q).encode(), timeout=300).read()
                break
            except Exception:
                time.sleep(2 ** (attempt + 1))
        else:
            raise RuntimeError(f"failed batch {i}")
        parts.append(gpd.read_file(body.decode()))
    g = pd.concat(parts, ignore_index=True)
    g = gpd.GeoDataFrame(g, geometry="geometry", crs="EPSG:4326")
    g["geometry"] = g.geometry.make_valid()
    g.to_file(path, driver="GPKG")
    return g


def points_in(g, df, idcol):
    pts = gpd.GeoDataFrame(df[[idcol]], geometry=gpd.points_from_xy(df.lon, df.lat), crs="EPSG:4326")
    hit = gpd.sjoin(pts, g[["id", "name", "type", "geometry"]], how="left", predicate="within")
    return hit.drop(columns=["geometry", "index_right"]).rename(columns={"id": "utility_id", "name": "utility_name",
                                                                         "type": "utility_type"})


def main():
    g = fetch_all()
    print(f"territories: {len(g):,}")
    dc = pd.read_csv(ROOT / "data/processed/datacenters_clean.csv")
    dc = dc[dc.treated_main & dc.lat.notna()]
    s = points_in(g, dc, "site_id").merge(dc[["site_id", "state", "wave", "target_state"]], on="site_id")
    OUT.mkdir(parents=True, exist_ok=True)
    s.to_csv(OUT / "site_utilities.csv", index=False)
    no_util = s.groupby("site_id").utility_id.apply(lambda x: x.notna().any())
    print(f"sites: {len(dc)}, in at least one territory: {no_util.mean():.1%}, "
          f"territories per site: {s.groupby('site_id').size().mean():.2f}")
    any_u = set(s.utility_id.dropna())
    wave_u = set(s[s.wave == "2023+"].utility_id.dropna())

    rows = []
    for st in STATES:
        base = ROOT / ("data/processed/nc" if st == "NC" else "data/processed/s22")
        c = pd.read_csv(base / f"{st.lower()}_2022_centers.csv")
        h = points_in(g, c, "precinct_id")
        h["site_any"] = h.utility_id.isin(any_u)
        h["site_wave"] = h.utility_id.isin(wave_u)
        flags = h.groupby("precinct_id").agg(n_utilities=("utility_id", "count"),
                                              shares_utility_any=("site_any", "any"),
                                              shares_utility_wave=("site_wave", "any"))
        h.to_csv(OUT / f"{st.lower()}_2022_utilities.csv", index=False)
        flags.reset_index().to_csv(OUT / f"{st.lower()}_2022_utility_flags.csv", index=False)
        rows.append({"state": st, "precincts": len(c), "no_territory": (flags.n_utilities == 0).mean().round(3),
                     "multi_territory": (flags.n_utilities > 1).mean().round(3),
                     "shares_any": flags.shares_utility_any.mean().round(3),
                     "shares_wave": flags.shares_utility_wave.mean().round(3)})
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
