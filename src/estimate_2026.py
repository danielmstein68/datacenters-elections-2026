"""Main 2022-to-2026 difference-in-differences, ready for 2026 results.

Inputs per state (st in lower case), once 2026 results are in:
  data/processed/s26/{st}_2026_results.csv   same columns as the 2022 results files
  data/processed/s26/{st}_2026_bg_xwalk.csv  2026 precinct -> 2020 block group crosswalk
                                             (built from the 2026 precinct map, as for 2022)
NC reads data/processed/nc/nc_2026_*.csv.

Unit: 2022 precincts. 2026 votes are moved onto them through block groups, as the placebo
moves 2018 votes (src/placebo.py:to_2022). When the 2026 results use the 2022 precinct ids
(same map), they are joined directly.

Rows: one per precinct and 2026 office (governor, senate, house). Baseline is the same office
in 2022. Where the office was not on the 2022 ballot (e.g. Senate in MI, MN, TX), the baseline is
set by BASELINE_FALLBACK below (open design question, currently the 2022 governor race).
House rows are dropped in TX and NC (redistricted) and where either year's race had only one
major-party candidate.

Outcomes (points):
  gop     change in GOP share of the two-party vote
  incparty  change in the two-party share of the party holding the seat in 2026
            (data/hand/incumbents_2026.csv)

Model: change = sum_k b_k [ring_k to nearest large site] + county-by-office effects,
weighted by 2022 two-party votes, standard errors clustered by county. Rings as in
src/rings.py; precincts more than 30 miles away in the same county are the reference.
Run with --test to feed the 2022 results in as 2026 (every coefficient should be 0).

Outputs: data/processed/results_2026/panel.csv, coefficients.csv
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from placebo import RINGS, ols_cluster, two_party, to_2022

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = PROC / "results_2026"
STATES = ["ga", "mi", "tx", "mn", "nc", "va", "az", "in", "wi"]
NO_HOUSE = {"tx", "nc"}
BASELINE_FALLBACK = "governor"  # 2022 office used when the 2026 office was not on the 2022 ballot


def paths(st, year):
    if st == "nc":
        base = PROC / "nc"
        return base / f"nc_{year}_results.csv", base / f"nc_{year}_bg_xwalk.csv"
    folder = {2022: "s22", 2026: "s26"}[year]
    return PROC / folder / f"{st}_{year}_results.csv", PROC / folder / f"{st}_{year}_bg_xwalk.csv"


def read(path, **kw):
    return pd.read_csv(path, low_memory=False, **kw)


def house_district(res):
    h = res[res.office == "house"].groupby(["precinct_id", "district"]).votes_allocated.sum().reset_index()
    return h.sort_values("votes_allocated").drop_duplicates("precinct_id", keep="last").set_index(
        "precinct_id").district


def contested(res):
    """Districts with both an R and a D candidate."""
    h = res[(res.office == "house") & res.party.isin(["R", "D"])]
    n = h.groupby("district").party.nunique()
    return set(n[n == 2].index)


def load_state(st, test=False):
    r22p, x22p = paths(st, 2022)
    r26p, x26p = paths(st, 2026)
    if test:
        r26p, x26p = r22p, x22p
    if not r26p.exists():
        print(f"{st.upper()}: no 2026 results yet")
        return None
    r22, x22 = read(r22p), read(x22p, dtype={"bg_geoid": str})
    r26, x26 = read(r26p), read(x26p, dtype={"bg_geoid": str})
    offices26 = sorted({"governor", "senate", "house"} & set(r26.office))
    if st in NO_HOUSE:
        offices26 = [o for o in offices26 if o != "house"]

    if set(r26.precinct_id) <= set(r22.precinct_id):
        v26 = two_party(r26, offices26)  # same map as 2022: no crosswalk needed
    else:
        v26 = to_2022(two_party(r26, offices26), x26, x22)
    base_office = {o: (o if o in set(r22.office) else BASELINE_FALLBACK) for o in offices26}
    v22 = two_party(r22, sorted(set(base_office.values())))
    rows = []
    for o, b in base_office.items():
        a = v26[v26.office == o].merge(v22[v22.office == b].drop(columns="office"), on="precinct_id",
                                       suffixes=("_26", "_22"))
        a["office"], a["baseline_office"] = o, b
        rows.append(a)
    p = pd.concat(rows, ignore_index=True)
    p["state"] = st.upper()
    county = r22.groupby("precinct_id").county.first()
    p["county"] = st.upper() + "_" + p.precinct_id.map(county).astype(str)

    if "house" in offices26:
        p["district"] = p.precinct_id.map(house_district(r22))
        ok = contested(r22) & contested(r26)
        p = p[(p.office != "house") | p.district.isin(ok)]

    inc = pd.read_csv(ROOT / "data/hand/incumbents_2026.csv")
    inc = inc[inc.state == st.upper()]
    statewide = inc[inc.office != "house"].set_index("office").incumbent_party
    house = inc[inc.office == "house"].set_index("district").incumbent_party
    p["inc_party"] = np.where(p.office == "house", p.get("district", pd.Series(index=p.index)).map(house),
                              p.office.map(statewide))

    p["tp_22"], p["tp_26"] = p.R_22 + p.D_22, p.R_26 + p.D_26
    p = p[(p.tp_22 > 0) & (p.tp_26 > 0)].copy()
    p["gop_22"], p["gop_26"] = 100 * p.R_22 / p.tp_22, 100 * p.R_26 / p.tp_26
    p["gop"] = p.gop_26 - p.gop_22
    p["incparty"] = np.where(p.inc_party == "R", p.gop, np.where(p.inc_party == "D", -p.gop, np.nan))

    rings = pd.read_csv(PROC / f"rings/{st}_2022_rings.csv")
    p = p.merge(rings[["precinct_id", "dist_any", "ring_any", "dist_wave", "ring_wave"]], on="precinct_id")
    load = PROC / f"utility/{st}_2022_utility_load.csv"
    if load.exists():  # utility-bill exposure; its model is still to be set
        p = p.merge(pd.read_csv(load), on="precinct_id", how="left")
    print(f"{st.upper()}: {p.precinct_id.nunique():,} precincts, offices {base_office}")
    return p


def fit(d, outcome, ring_col, label):
    d = d[d[outcome].notna()]
    X = pd.DataFrame({r: (d[ring_col] == r).astype(float) for r in RINGS}, index=d.index)
    X = pd.concat([X, pd.get_dummies(d.county + ":" + d.office, dtype=float)], axis=1)
    b, se, G = ols_cluster(d[outcome].to_numpy(float), X.to_numpy(float), d.tp_22.to_numpy(float),
                           d.county.to_numpy())
    out = pd.DataFrame({"ring": RINGS, "coef": b[:len(RINGS)], "se": se[:len(RINGS)]})
    out["n_rows"] = out.ring.map(d[ring_col].value_counts()).fillna(0).astype(int)
    out.insert(0, "spec", label)
    out.insert(1, "outcome", outcome)
    out["n_total"], out["clusters"] = len(d), G
    return out


def fit_utility(d, outcome, ratio_col, label):
    """Utility-bill test (placebo-tested 2026-11-08): log(1 + data center load / utility load),
    compared within state and office, with ring dummies and the 2022 GOP share and its square."""
    d = d[d[outcome].notna() & d[ratio_col].notna()]
    X = pd.DataFrame({"exposure": np.log1p(d[ratio_col])}, index=d.index)
    for r in RINGS:
        X[f"ring_{r}"] = (d.ring_any == r).astype(float)
    X["g22"], X["g22sq"] = d.gop_22, d.gop_22 ** 2 / 100
    X = pd.concat([X, pd.get_dummies(d.state + ":" + d.office, dtype=float)], axis=1)
    b, se, G = ols_cluster(d[outcome].to_numpy(float), X.to_numpy(float), d.tp_22.to_numpy(float),
                           d.county.to_numpy())
    return pd.DataFrame([{"spec": label, "outcome": outcome, "ring": "utility exposure (per log unit)",
                          "coef": b[0], "se": se[0], "n_rows": len(d), "n_total": len(d), "clusters": G}])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true", help="use 2022 results as 2026 (all effects should be 0)")
    args = ap.parse_args()
    panel = [p for st in STATES if (p := load_state(st, args.test)) is not None]
    if not panel:
        return
    panel = pd.concat(panel, ignore_index=True)
    specs = []
    for outcome in ("gop", "incparty"):
        specs.append(fit(panel, outcome, "ring_any", "all offices"))
        specs.append(fit(panel, outcome, "ring_wave", "all offices, rings to 2023+ sites"))
        for o in sorted(panel.office.unique()):
            specs.append(fit(panel[panel.office == o], outcome, "ring_any", o))
        if "dc_load_ratio_all" in panel:
            specs.append(fit_utility(panel, outcome, "dc_load_ratio_all", "utility bill, all large sites"))
            specs.append(fit_utility(panel, outcome, "dc_load_ratio_wave", "utility bill, 2023+ sites"))
    coef = pd.concat(specs, ignore_index=True)
    out = OUT / ("test" if args.test else "")
    out.mkdir(parents=True, exist_ok=True)
    panel.round(4).to_csv(out / "panel.csv", index=False)
    coef.round(3).to_csv(out / "coefficients.csv", index=False)
    print(coef[coef.spec == "all offices"].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
