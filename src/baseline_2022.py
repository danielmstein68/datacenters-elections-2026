"""2022 precinct results and geography for GA, MI, TX, IA, MN, NE and VA from free sources.

VEST's 2022 files are sold by subscription (UF Election Lab), so 2022 is built from free
sources instead (decided 2026-10-31):

  state  results                                   precinct map
  TX     Texas Legislative Council VTD returns     TLC 2022 general VTDs (same keys, exact join)
  MN     MEDSL 2022 precinct returns               LCC 2022 general VTDs
  MI     OpenElections 2022 precinct file          Michigan GIS "2022 Voting Precincts"
  GA, IA, NE, VA
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
MEDSL = {"GA": 10855164, "IA": 10855151, "MN": 10855150, "NE": 10855155, "VA": 10855178}
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
              "GA": "census_vtd_2020", "IA": "census_vtd_2020", "NE": "census_vtd_2020",
              "VA": "census_vtd_2020"}


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


def medsl(st):
    path = fetch(f"https://dataverse.harvard.edu/api/access/datafile/{MEDSL[st]}?format=original",
                 RAW / f"medsl/{st.lower()}.tab")
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


def load_mi():
    path = RAW / "mi/mi_precincts_2022.geojson"
    if not path.exists():
        feats, off = [], 0
        while True:
            q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson",
                                        "resultOffset": off, "resultRecordCount": 1000})
            req = urllib.request.Request(f"{MI_MAP}?{q}", headers=UA)
            fs = json.load(urllib.request.urlopen(req, timeout=300))["features"]
            feats += fs
            off += len(fs)
            if len(fs) < 1000:
                break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    v = gpd.read_file(path)
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
    if st == "TX":
        precincts, res = load_tx()
        res["match_step"] = "key"
    else:
        if st == "MN":
            precincts, res = load_mn()
        elif st == "MI":
            precincts, res = load_mi()
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
