"""2022 precinct results and geography for GA, MI, TX, MN, VA, AZ, IN and WI from free sources.

VEST's 2022 files are sold by subscription (UF Election Lab), so 2022 is built from free
sources instead (decided 2026-10-31):

  state  results                                   precinct map
  TX     Texas Legislative Council VTD returns     TLC 2022 general VTDs (same keys, exact join)
  MN     MEDSL 2022 precinct returns               LCC 2022 general VTDs
  MI     OpenElections 2022 precinct file          Michigan GIS "2022 Voting Precincts"
  WI     LTSB 2022 wards with results attached  same file (exact join)
  IN     MEDSL 2022 (national Senate/House files)  IndianaMap 2023 voting districts
  AZ     MEDSL 2022 precinct returns               Maricopa 2022 and Pima 2024 precincts; 2020 VTDs elsewhere
  GA, VA
         MEDSL 2022 precinct returns               2020 census voting districts (TIGER VTD20)

Outside TX, results carry precinct names but no map keys, so names are matched to map
precincts within each county in steps, and the step used is recorded:
  exact   normalized name (case, punctuation, Township/Twp, St/Saint, zero padding, ...)
  sorted  same words in any order ("Charter Township of Canton" = "Canton Charter Township")
  noward  same, ignoring ward numbers on both sides
  split   result names with a trailing letter (3A, 3B) summed into the map precinct 3
  single  a one-precinct township named without "Precinct 1"
  code    leading precinct number alone, against census VTD codes (VA style "319 - Name")
  fuzzy   best close match within the county (difflib ratio >= 0.85), only if unique
Votes that still have no map precinct (county-wide absentee units, renamed or new precincts)
are spread across the county's matched precincts in proportion to each candidate's matched
votes, the same rule as src/baseline_nc.py. The share of votes placed this way is reported per
state in data/processed/s22/match_report.csv. Where a 2020 census VTD map stands in for the
2022 precincts, matched votes sit in 2020 boundaries, so those states are rougher.

Outputs in data/processed/s22/, matching src/baseline_vest.py:
  {st}_2022_results.csv  year, state, precinct_id, county, office, district, contest,
                         candidate_code, party, votes_real, votes_allocated
  {st}_2022_centers.csv  precinct_id, pop20, lat, lon, center_method
  {st}_2022_bg_xwalk.csv precinct_id, bg_geoid, pop20, share_of_precinct, share_of_bg
"""

import argparse
import difflib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

from baseline_vest import (BLOCKS, STATES, allocate_pooled, bg_crosswalk, centers,
                           download_blocks, match_blocks, read_zip_shapefile)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/s22"
OUT = ROOT / "data/processed/s22"
UA = {"User-Agent": "datacenters-elections-2026/1.0"}

# MEDSL 2022 precinct returns by state, CC0 (doi:10.7910/DVN/UYQIEP), Dataverse file ids.
MEDSL = {"GA": 10855164, "MN": 10855150, "VA": 10855178, "WI": 10855181,
         "AZ": 14249794}  # AZ: the "local" file, which also holds the federal and state races
# MEDSL's by-state set has no Indiana file; IN comes from the national Senate and House files.
MEDSL_NATIONAL = {"senate": 13996913, "house": 13996912}
MARICOPA_MAP = ("https://services.arcgis.com/ykpntM6e3tHvzKRJ/arcgis/rest/services/"
                "Maricopa_County_Voting_Precincts_(2022-)/FeatureServer/0/query")
# Pima's map is its Oct 2024 election polygons, dissolved to voting precincts. Its precinct
# numbers match 264 of 266 Pima precincts in the 2022 results.
PIMA_MAP = ("https://services2.arcgis.com/UTBp78iglGpbqp1B/arcgis/rest/services/"
            "election_polys_20241004_view/FeatureServer/0/query")
IN_MAP = "https://gisdata.in.gov/server/rest/services/Hosted/Voting_District_Boundaries_2023/FeatureServer/1/query"
WI_MAP = ("https://services1.arcgis.com/FDsAtKBk8Hy4cAH0/arcgis/rest/services/"
          "2022_Election_Data_wtih_2022_Wards/FeatureServer/0/query")
TX_VTD = ("https://data.capitol.texas.gov/dataset/4d8298d0-d176-4c19-b174-42837027b73e/"
          "resource/037e1de6-a862-49de-ae31-ae609e214972/download/vtds_22g.zip")
TX_RES = ("https://data.capitol.texas.gov/dataset/35b16aee-0bb0-4866-b1ec-859f1f044241/"
          "resource/b9ebdbdb-3e31-4c98-b158-0e2993b05efc/download/2022-general-vtds-election-data.zip")
MN_VTD = "https://gis.lcc.mn.gov/data/shape/vtd2022general.zip"
MI_MAP = "https://gisagocss.state.mi.us/arcgis/rest/services/OpenData/boundaries/MapServer/7/query"
MI_RES = ("https://raw.githubusercontent.com/openelections/openelections-data-mi/master/2022/"
          "20221108__mi__general__precinct.csv")
VTD20 = "https://www2.census.gov/geo/tiger/TIGER2020PL/LAYER/VTD/2020/tl_2020_{fips}_vtd20.zip"
COUNTY_LIST = "https://www2.census.gov/geo/docs/reference/codes2020/cou/st{fips}_{st}_cou2020.txt"

MAP_SOURCE = {"TX": "tlc_vtd_2022", "MN": "lcc_vtd_2022", "MI": "mi_precincts_2022",
              "GA": "census_vtd_2020", "VA": "census_vtd_2020",
              "AZ": "maricopa_2022+pima_2024+census_vtd_2020", "IN": "indianamap_2023", "WI": "ltsb_wards_2022"}


def fetch(url, path):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
            path.write_bytes(r.read())
    return path


# ---------- name matching ----------

SUBS = [(r"\(.*?\)", " "), (r"\bCITY OF\b", " "), (r"\bVILLAGE OF\b", " "), (r"\bTOWNSHIP OF\b", " TWP "),
        (r"\bCHARTER\b", " "), (r"\bTOWNSHIP\b", "TWP"), (r"\bST\b\.?", "SAINT"), (r"\bSTE\b", "SAINTE"),
        (r"\bHTS\b", "HEIGHTS"), (r"\bMT\b", "MOUNT"), (r"\bFT\b", "FORT"), (r"\bCITY\b", " "),
        (r"\bALL PRECINCTS\b", " "), (r"\b(PRECINCT|PCT|PREC|P)\s*-?\s*(?=\d)", " # "),
        (r"\b(WARD|W)\s*-?\s*(?=\d)", " ~ ")]


def tokens(s):
    """Name -> tokens: words, '#n' for precinct numbers, '~n' for ward numbers."""
    s = str(s).upper()
    for a, b in SUBS:
        s = re.sub(a, b, s)
    raw = re.findall(r"[A-Z]+|\d+[A-Z]?|#|~", s)
    out, mark = [], ""
    for t in raw:
        if t in "#~":
            mark = t
            continue
        if t[0].isdigit():
            t = mark + (t.lstrip("0") or "0")
        out.append(t)
        mark = ""
    # A bare trailing number ("SHELBY TOWNSHIP 24") is a precinct number.
    if out and out[-1][0].isdigit():
        out[-1] = "#" + out[-1]
    return out


def norm(s):
    return "".join(tokens(s))


def key_sorted(t):
    return "".join(sorted(t))


def key_noward(t):
    return key_sorted([x for x in t if not x.startswith("~")])


def key_split(t):
    return key_sorted([re.sub(r"(?<=\d)[A-Z]$", "", x) if x.startswith("#") else x
                       for x in t if not x.startswith("~")])


def key_single(t):
    return key_sorted([x for x in t if x not in ("TWP", "#1")])


def key_code(t):
    """Leading precinct number alone ("319 - John Champe" -> #319), for code-numbered VTDs."""
    if t and t[0].lstrip("#")[:1].isdigit():
        return "#" + t[0].lstrip("#")
    return None


def match_names(res_names, map_names):
    """res_names: DataFrame county, name. map_names: DataFrame county, name, precinct_id.
    Returns res_names with precinct_id and match_step (None where unmatched)."""
    out = res_names.copy()
    out["precinct_id"], out["match_step"] = None, None
    m = map_names.assign(t=map_names["name"].map(tokens))
    m["k"] = m["t"].map("".join)
    out["t"] = out["name"].map(tokens)
    out["k"] = out["t"].map("".join)
    steps = [("exact", "".join), ("sorted", key_sorted), ("noward", key_noward),
             ("split", key_split), ("single", key_single), ("code", key_code)]
    for step, f in steps:
        todo = out["precinct_id"].isna()
        lookup = m.assign(kk=m["t"].map(f)).dropna(subset=["kk"])
        lookup = lookup.drop_duplicates(["county", "kk", "precinct_id"])
        lookup = lookup.drop_duplicates(["county", "kk"], keep=False)
        lookup = lookup.set_index(["county", "kk"])["precinct_id"]
        keys = list(zip(out.loc[todo, "county"], out.loc[todo, "t"].map(f)))
        hit = pd.Series([lookup.get(k) for k in keys], index=out.index[todo], dtype=object)
        out.loc[hit.dropna().index, "precinct_id"] = hit.dropna()
        out.loc[hit.dropna().index, "match_step"] = step
    by_cty = {c: g for c, g in m.groupby("county")}
    for i in out.index[out["precinct_id"].isna()]:
        g = by_cty.get(out.at[i, "county"])
        if g is None or not out.at[i, "k"]:
            continue
        close = difflib.get_close_matches(out.at[i, "k"], g["k"].tolist(), n=2, cutoff=0.85)
        if len(close) == 1 or (len(close) == 2 and
                               difflib.SequenceMatcher(None, out.at[i, "k"], close[1]).ratio() < 0.85):
            out.at[i, "precinct_id"] = g.loc[g["k"] == close[0], "precinct_id"].iloc[0]
            out.at[i, "match_step"] = "fuzzy"
    return out.drop(columns=["k", "t"])


# ---------- per-state loaders: return (map GeoDataFrame, long results) ----------
# map: precinct_id, county (5-digit FIPS), name, geometry
# results: county, name (or precinct_id for TX), office, district, candidate, party, votes


NOT_CANDIDATES = ["Ballots Cast", "Cast Votes", "Registered Voters", "Total", "Under Votes", "Over Votes",
                  "Undervotes", "Overvotes", "Rejected Write-Ins", "Blank", ""]


def party_code(p):
    p = str(p).upper()
    return "D" if p.startswith("DEM") or p in ("D", "DFL") else "R" if p.startswith("REP") or p == "R" else "O"


def load_tx():
    vtd = read_zip_shapefile(fetch(TX_VTD, RAW / "tx/vtds_22g.zip"))
    vtd["precinct_id"] = "TX22_" + vtd["CNTYVTD"].astype(str)
    vtd["county"] = "48" + vtd["CNTY"].astype(int).astype(str).str.zfill(3)
    vtd["name"] = vtd["CNTYVTD"].astype(str)
    import zipfile
    with zipfile.ZipFile(fetch(TX_RES, RAW / "tx/el22g.zip")) as z:
        r = pd.read_csv(z.open("2022_General_Election_Returns.csv"), dtype={"cntyvtd": str})
    r = r[(r.Office == "Governor") | r.Office.str.match(r"U\.S\. Rep \d+$")]
    r = pd.DataFrame({
        "precinct_id": "TX22_" + r["cntyvtd"],
        "county": ("TX22_" + r["cntyvtd"]).map(dict(zip(vtd.precinct_id, vtd.county))),
        "office": r.Office.map(lambda o: "governor" if o == "Governor" else "house"),
        "district": r.Office.str.extract(r"(\d+)$")[0].astype(float),
        "candidate": r.Name, "party": r.Party.map(party_code), "votes": r.Votes,
    })
    return vtd[["precinct_id", "county", "name", "geometry"]], r


def medsl_national(st):
    """Pull one state's rows out of MEDSL's national Senate and House files, once."""
    path = RAW / f"medsl/{st.lower()}.tab"
    if not path.exists():
        parts = []
        for office, fid in MEDSL_NATIONAL.items():
            big = fetch(f"https://dataverse.harvard.edu/api/access/datafile/{fid}?format=original",
                        RAW / f"medsl/national_{office}.tab")
            for ch in pd.read_csv(big, dtype=str, chunksize=500_000):
                parts.append(ch[ch.state_po == st])
            big.unlink()
        pd.concat(parts).to_csv(path, index=False)
    return path


def medsl(st):
    if st in MEDSL:
        path = fetch(f"https://dataverse.harvard.edu/api/access/datafile/{MEDSL[st]}?format=original",
                     RAW / f"medsl/{st.lower()}.tab")
    else:
        path = medsl_national(st)
    d = pd.read_csv(path, dtype=str, low_memory=False)
    d = d[d.office.isin(["US SENATE", "GOVERNOR", "US HOUSE"]) & (d.stage == "GEN")]
    d = d[d.candidate.notna() & ~d.candidate.str.upper().isin([c.upper() for c in NOT_CANDIDATES])]
    # Some files give both a TOTAL row and per-mode rows; keep TOTAL where it exists.
    k = ["county_fips", "precinct", "office", "district", "candidate"]
    has_total = d.assign(t=d["mode"].str.upper() == "TOTAL").groupby(k, dropna=False)["t"].transform("any")
    d = d[~has_total | (d["mode"].str.upper() == "TOTAL")]
    off = d.office.map({"US SENATE": "senate", "GOVERNOR": "governor", "US HOUSE": "house"})
    off = off.where(d.special.str.upper() != "TRUE", off + "_special")
    return pd.DataFrame({
        "county": d.county_fips.str.zfill(5), "name": d.precinct, "office": off,
        "district": pd.to_numeric(d.district.where(d.office == "US HOUSE"), errors="coerce"),
        "candidate": d.candidate, "party": d.party_detailed.map(party_code),
        "votes": pd.to_numeric(d.votes, errors="coerce").fillna(0),
    })


def load_mn():
    v = read_zip_shapefile(fetch(MN_VTD, RAW / "mn/vtd2022general.zip"))
    v["precinct_id"] = "MN22_" + v["VTDID"].astype(str)
    v["county"] = "27" + v["COUNTYFIPS"].astype(str).str.zfill(3)
    v["name"] = v["PCTNAME"]
    return v[["precinct_id", "county", "name", "geometry"]], medsl("MN")


def load_vtd20(st):
    fips = STATES[st]
    v = read_zip_shapefile(fetch(VTD20.format(fips=fips), RAW / f"vtd20/tl_2020_{fips}_vtd20.zip"))
    v["precinct_id"] = f"{st}20VTD_" + v["GEOID20"]
    v["county"] = v["STATEFP20"] + v["COUNTYFP20"]
    # Results may use either the VTD name or its code; offer both as map names.
    a = v.assign(name=v["NAME20"])
    b = v.assign(name=v["VTDST20"].str.lstrip("0"))
    names = pd.concat([a, b])[["precinct_id", "county", "name"]]
    return v[["precinct_id", "county", "NAME20", "geometry"]].rename(columns={"NAME20": "name"}), names


def arcgis_layer(url, path, page=1000):
    """Download every feature of an ArcGIS feature layer as GeoJSON, once."""
    path = Path(path)
    if not path.exists():
        feats, off = [], 0
        while True:
            q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                                        "resultOffset": off, "resultRecordCount": page})
            req = urllib.request.Request(f"{url}?{q}", headers=UA)
            fs = json.load(urllib.request.urlopen(req, timeout=300))["features"]
            feats += fs
            off += len(fs)
            if len(fs) < page:
                break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return gpd.read_file(path)


def load_az():
    """Maricopa's 2022 precinct map and Pima's 2024 one (together about 77% of AZ votes);
    2020 census VTDs for the other counties."""
    m = arcgis_layer(MARICOPA_MAP, RAW / "az/maricopa_precincts_2022.geojson")
    m["precinct_id"] = "AZ22MC_" + m["BdVal"].astype(str)
    m["county"] = "04013"
    m["name"] = m["BdVal"].astype(str).str.zfill(4) + " " + m["BdName"]
    pm = arcgis_layer(PIMA_MAP, RAW / "az/pima_election_polys_2024.geojson")
    pm = pm[pm["POLL_PCT_STR"].notna()].dissolve(by="POLL_PCT_STR", as_index=False)
    pm["precinct_id"] = "AZ24PC_" + pm["POLL_PCT_STR"].astype(str)
    pm["county"] = "04019"
    pm["name"] = pm["POLL_PCT_STR"].astype(str)
    v, names = load_vtd20("AZ")
    keep = ~v.county.isin(["04013", "04019"])
    v, names = v[keep], names[~names.county.isin(["04013", "04019"])]
    cols = ["precinct_id", "county", "name"]
    precincts = pd.concat([m[cols + ["geometry"]], pm[cols + ["geometry"]].to_crs(m.crs),
                           v.to_crs(m.crs)], ignore_index=True)
    names = pd.concat([pm[cols], names])
    return gpd.GeoDataFrame(precincts, crs=m.crs), pd.concat([m[cols], names]), medsl("AZ")


def load_in():
    v = arcgis_layer(IN_MAP, RAW / "in/in_precincts_2023.geojson")
    v["precinct_id"] = "IN23_" + v["p23_stfid"].astype(str)
    v["county"] = "18" + v["county"].astype(str).str.zfill(3)
    v["name"] = v["p23"]
    return v[["precinct_id", "county", "name", "geometry"]], medsl("IN")


def load_wi():
    """LTSB 2022 wards with 2022 results already attached (exact keys, like TX)."""
    v = arcgis_layer(WI_MAP, RAW / "wi/wi_wards_2022.geojson", page=2000)
    v["precinct_id"] = "WI22_" + v["GEOID"].astype(str)
    v["county"] = "55" + v["CNTY_FIPS"].astype(str).str.zfill(3)
    v["name"] = v["LABEL"]
    rows = []
    for pre, office in [("GOV", "governor"), ("USS", "senate"), ("USH", "house")]:
        cols = [c for c in v.columns if c.startswith(pre) and c.endswith("22") and c != f"{pre}TOT22"]
        long = v[["precinct_id", "county", "CON2021"] + cols].melt(
            id_vars=["precinct_id", "county", "CON2021"], var_name="candidate", value_name="votes")
        long["office"] = office
        long["party"] = long.candidate.str[3:6].map({"DEM": "D", "REP": "R"}).fillna("O")
        long["district"] = pd.to_numeric(long["CON2021"], errors="coerce") if office == "house" else float("nan")
        rows.append(long.drop(columns="CON2021"))
    r = pd.concat(rows)
    r["votes"] = pd.to_numeric(r["votes"], errors="coerce").fillna(0)
    return v[["precinct_id", "county", "name", "geometry"]], r


def load_mi():
    v = arcgis_layer(MI_MAP, RAW / "mi/mi_precincts_2022.geojson")
    v["precinct_id"] = "MI22_" + v["PRECINCTID"]
    v["county"] = "26" + v["COUNTYFIPS"].str.zfill(3)
    v["name"] = v["Precinct_Long_Name"]

    cty = pd.read_csv(fetch(COUNTY_LIST.format(fips="26", st="mi"), RAW / "mi/counties.txt"),
                      sep="|", dtype=str)
    to_fips = dict(zip(cty.COUNTYNAME.str.replace(" County", "").str.upper().str.replace(".", ""),
                       "26" + cty.COUNTYFP))
    r = pd.read_csv(fetch(MI_RES, RAW / "mi/oe_mi_2022.csv"), dtype=str)
    r = r[r.office.isin(["Governor", "U.S. House"])]
    r = r[~r.candidate.fillna("").isin(NOT_CANDIDATES)]
    # Precincts listed under a neighbouring county carry "(X County)"; use that county.
    other = r.precinct.str.extract(r"\((\w[\w. ]*?) County\)")[0].str.upper()
    cname = other.fillna(r.county.str.upper()).str.replace(".", "", regex=False)
    r = pd.DataFrame({
        "county": cname.map(to_fips), "name": r.precinct,
        "office": r.office.map({"Governor": "governor", "U.S. House": "house"}),
        "district": pd.to_numeric(r.district, errors="coerce"), "candidate": r.candidate,
        "party": r.party.map(party_code), "votes": pd.to_numeric(r.votes, errors="coerce").fillna(0),
    })
    return v[["precinct_id", "county", "name", "geometry"]], r


# ---------- build ----------

def build(st, blocks):
    names = None
    if st in ("TX", "WI"):
        precincts, res = load_tx() if st == "TX" else load_wi()
        res["match_step"] = "key"
    else:
        if st == "MN":
            precincts, res = load_mn()
        elif st == "MI":
            precincts, res = load_mi()
        elif st == "IN":
            precincts, res = load_in()
        elif st == "AZ":
            precincts, names, res = load_az()
        else:
            precincts, names = load_vtd20(st)
            res = medsl(st)
        if names is None:
            names = precincts[["precinct_id", "county", "name"]]
        units = res[["county", "name"]].drop_duplicates()
        units = match_names(units, names)
        res = res.merge(units, on=["county", "name"], how="left")
    precincts = precincts.dissolve(by="precinct_id", as_index=False, aggfunc="first")

    res["contest"] = res["office"].where(res["office"].str.startswith(("governor", "senate")),
                                         "house_" + res["district"].fillna(0).astype(int).astype(str).str.zfill(2))
    special = res["office"].str.endswith("_special")
    res.loc[special & ~res["contest"].str.endswith("_special"), "contest"] += "_special"
    res["office"] = res["office"].str.replace("_special", "", regex=False)
    real = res["precinct_id"].notna()
    res["precinct_id"] = res["precinct_id"].fillna("UNMATCHED_" + res["county"].astype(str))
    long = (res.groupby(["precinct_id", "county", "office", "district", "contest", "candidate", "party"],
                        dropna=False).votes.sum().reset_index())
    long["real"] = ~long["precinct_id"].str.startswith("UNMATCHED_")
    long = long.rename(columns={"candidate": "candidate_code"})
    long, pooled, lost = allocate_pooled(long, long["real"])

    total = res.votes.sum()
    steps = res.groupby(res["match_step"].fillna("unmatched")).votes.sum() / total
    report = {"state": st, "map_source": MAP_SOURCE[st], "map_precincts": len(precincts),
              "result_units": res[["county", "name"]].drop_duplicates().shape[0] if "name" in res else None,
              "votes": total, "share_unmatched_spread": round(pooled / total, 4),
              "votes_unplaceable": round(lost, 1),
              **{f"share_{k}": round(v, 4) for k, v in steps.items()}}
    print(st, report)

    long.insert(0, "state", st)
    long.insert(0, "year", 2022)
    long["district"] = long["district"].astype("Int64")
    long["votes_allocated"] = long["votes_allocated"].round(2)
    results = long.rename(columns={"votes": "votes_real"})[
        ["year", "state", "precinct_id", "county", "office", "district", "contest",
         "candidate_code", "party", "votes_real", "votes_allocated"]]

    hit = match_blocks(precincts, blocks)
    OUT.mkdir(parents=True, exist_ok=True)
    precincts.to_file(OUT / f"{st.lower()}_2022_precincts.gpkg", driver="GPKG")
    results.to_csv(OUT / f"{st.lower()}_2022_results.csv", index=False)
    centers(precincts, hit).to_csv(OUT / f"{st.lower()}_2022_centers.csv", index=False)
    bg_crosswalk(hit).to_csv(OUT / f"{st.lower()}_2022_bg_xwalk.csv", index=False)
    print(results.groupby(["office", "party"]).votes_allocated.sum().round(0).to_string())
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--states", nargs="+", default=list(STATES))
    a = p.parse_args()
    reports = []
    for st in a.states:
        blocks = read_zip_shapefile(download_blocks(st))
        reports.append(build(st, blocks))
    path = OUT / "match_report.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["state"])
    new = pd.concat([old[~old.state.isin(a.states)], pd.DataFrame(reports)]).sort_values("state")
    new.to_csv(path, index=False)


if __name__ == "__main__":
    main()
