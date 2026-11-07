"""2018-to-2022 placebo: did precincts near 2023+ data centers already shift before those projects existed?

Unit: 2022 precincts (the baseline geography for the 2026 analysis). 2018 votes are moved onto
2022 precincts through 2020 census block groups: each 2018 precinct's votes go to its block
groups by population share (2018 crosswalk, share_of_precinct), then each block group's votes
go to the 2022 precincts that hold its people (2022 crosswalk, share_of_bg). Both crosswalks
are built from the same 2020 blocks.

Offices: those on the ballot in both years in a state (governor and/or U.S. Senate).
North Carolina uses its partisan statewide Supreme Court races instead (all seats pooled).
Virginia has no placebo office (2018 Senate only, 2022 House only).

Outcome: change in the Republican share of the two-party vote, 2018 to 2022, in points.

Model (OLS, one row per precinct and office):
    change = sum_k b_k * [ring_k to nearest 2023+ site] + county-by-office effects (+ controls)
Rings 0-1, 1-2, 2-5, 5-10, 10-20, 20-30 mi; precincts more than 30 mi away are the reference.
Standard errors are clustered by county. Main runs weight by 2022 two-party votes.

Three ways of handling sites that were already operating before the wave are reported,
because older sites could have moved votes between 2018 and 2022 on their own:
    A  ignore pre-wave sites
    B  add dummies for the ring to the nearest pre-wave site
    C  drop precincts within 20 mi of a pre-wave site
Precincts are compared only with other precincts in the same county (decided by Daniel on
2026-11-06, after state-by-office comparisons failed this placebo with a flat 2-3 point gap in
every ring, a metro-versus-rural trend). The state-by-office runs are still reported as S.

Outputs: data/processed/placebo/panel.csv, coefficients.csv, ring_means.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = PROC / "placebo"
RINGS = ["0-1", "1-2", "2-5", "5-10", "10-20", "20-30"]
REF = "30+"
VEST_STATES = ["ga", "mi", "tx", "mn", "va", "az", "in", "wi"]


def two_party(res, offices):
    r = res[res.office.isin(offices) & res.party.isin(["R", "D"])]
    if "contest" in r:
        # MN 2018 had a regular and a special Senate race; keep only the regular one.
        r = r[~r.contest.astype(str).str.contains("special", case=False)]
    w = r.pivot_table(index=["precinct_id", "office"], columns="party", values="votes_allocated",
                      aggfunc="sum", fill_value=0)
    return w.reindex(columns=["R", "D"], fill_value=0).reset_index()


def to_2022(v18, x18, x22):
    """Move 2018 precinct votes onto 2022 precincts through block groups."""
    bg = v18.merge(x18[["precinct_id", "bg_geoid", "share_of_precinct"]], on="precinct_id")
    for p in ("R", "D"):
        bg[p] = bg[p] * bg.share_of_precinct
    bg = bg.groupby(["bg_geoid", "office"])[["R", "D"]].sum().reset_index()
    p22 = bg.merge(x22[["precinct_id", "bg_geoid", "share_of_bg"]], on="bg_geoid")
    for p in ("R", "D"):
        p22[p] = p22[p] * p22.share_of_bg
    return p22.groupby(["precinct_id", "office"])[["R", "D"]].sum().reset_index()


def load_state(st):
    if st == "nc":
        base = PROC / "nc"
        r18, r22 = pd.read_csv(base / "nc_2018_results.csv"), pd.read_csv(base / "nc_2022_results.csv")
        x18, x22 = pd.read_csv(base / "nc_2018_bg_xwalk.csv"), pd.read_csv(base / "nc_2022_bg_xwalk.csv")
        offices = ["nc_supreme_court"]
    else:
        r18 = pd.read_csv(PROC / f"vest/{st}_2018_results.csv", low_memory=False)
        r22 = pd.read_csv(PROC / f"s22/{st}_2022_results.csv", low_memory=False)
        x18 = pd.read_csv(PROC / f"vest/{st}_2018_bg_xwalk.csv", dtype={"bg_geoid": str})
        x22 = pd.read_csv(PROC / f"s22/{st}_2022_bg_xwalk.csv", dtype={"bg_geoid": str})
        offices = sorted({"governor", "senate"} & set(r18.office) & set(r22.office))
    x18["bg_geoid"], x22["bg_geoid"] = x18.bg_geoid.astype(str), x22.bg_geoid.astype(str)
    if not offices:
        print(f"{st.upper()}: no office on the ballot in both years, skipped")
        return None
    v18 = to_2022(two_party(r18, offices), x18, x22)
    v22 = two_party(r22, offices)
    p = v22.merge(v18, on=["precinct_id", "office"], suffixes=("_22", "_18"))
    county = r22.groupby("precinct_id").county.first()
    p["county"] = st.upper() + "_" + p.precinct_id.map(county).astype(str)
    p["state"] = st.upper()
    p["tp_22"], p["tp_18"] = p.R_22 + p.D_22, p.R_18 + p.D_18
    p = p[(p.tp_22 > 0) & (p.tp_18 > 0)].copy()
    p["rshare_18"] = 100 * p.R_18 / p.tp_18
    p["rshare_22"] = 100 * p.R_22 / p.tp_22
    p["change"] = p.rshare_22 - p.rshare_18
    rings = pd.read_csv(PROC / f"rings/{st}_2022_rings.csv")
    p = p.merge(rings[["precinct_id", "dist_wave", "ring_wave", "dist_prewave", "ring_prewave"]],
                on="precinct_id", how="inner")
    tot18 = two_party(r18, offices).assign(tp=lambda d: d.R + d.D).groupby("office").tp.sum()
    print(f"{st.upper()} {', '.join(offices)}: {p.precinct_id.nunique():,} precincts; 2018 two-party votes "
          f"carried onto 2022 precincts: {(p.groupby('office').tp_18.sum() / tot18).round(3).to_dict()}")
    return p


def ols_cluster(y, X, w, cl):
    sw = np.sqrt(w)
    Xw, yw = X * sw[:, None], y * sw
    XtX_inv = np.linalg.pinv(Xw.T @ Xw)
    b = XtX_inv @ Xw.T @ yw
    e = yw - Xw @ b
    codes = pd.factorize(cl)[0]
    G = codes.max() + 1
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, codes, Xw * e[:, None])
    n, k = X.shape
    adj = G / (G - 1) * (n - 1) / (n - k)
    V = adj * XtX_inv @ (S.T @ S) @ XtX_inv
    return b, np.sqrt(np.diag(V)), G


def fit(p, label, weighted=True, prewave_dummies=False, county_fe=True):
    d = p.copy()
    cols = [f"wave_{r}" for r in RINGS]
    X = pd.DataFrame({f"wave_{r}": (d.ring_wave == r).astype(float) for r in RINGS}, index=d.index)
    if prewave_dummies:
        for r in RINGS:
            X[f"prewave_{r}"] = (d.ring_prewave == r).astype(float)
    fe = pd.get_dummies((d.county if county_fe else d.state) + ":" + d.office, dtype=float)
    X = pd.concat([X, fe], axis=1)
    w = d.tp_22.to_numpy(float) if weighted else np.ones(len(d))
    b, se, G = ols_cluster(d.change.to_numpy(float), X.to_numpy(float), w, d.county.to_numpy())
    out = pd.DataFrame({"term": X.columns, "coef": b, "se": se})
    out = out[out.term.isin(cols)].assign(ring=lambda t: t.term.str.replace("wave_", ""))
    n = d.ring_wave.value_counts()
    out["n_rows"] = out.ring.map(n).fillna(0).astype(int)
    out["t"] = out.coef / out.se
    out.insert(0, "spec", label)
    out["n_total"], out["clusters"] = len(d), G
    return out.drop(columns="term")


def main():
    states = VEST_STATES + (["nc"] if (PROC / "nc/nc_2022_results.csv").exists() else [])
    panel = pd.concat([p for st in states if (p := load_state(st)) is not None], ignore_index=True)
    OUT.mkdir(parents=True, exist_ok=True)
    panel.round(4).to_csv(OUT / "panel.csv", index=False)

    far_from_old = panel.dist_prewave >= 20
    specs = [
        fit(panel, "A ignore pre-wave"),
        fit(panel, "B pre-wave ring dummies", prewave_dummies=True),
        fit(panel[far_from_old], "C drop <20 mi of pre-wave"),
        fit(panel, "A unweighted", weighted=False),
        fit(panel, "S state-by-office (failed plan)", county_fe=False),
    ]
    for st in sorted(panel.state.unique()):
        s = panel[panel.state == st]
        specs.append(fit(s, f"A {st} only"))
    coef = pd.concat(specs, ignore_index=True)
    coef.round(3).to_csv(OUT / "coefficients.csv", index=False)

    means = (panel.assign(w=panel.tp_22, wc=panel.change * panel.tp_22)
             .groupby(["ring_wave"])[["wc", "w"]].sum())
    means = pd.DataFrame({"mean_change_weighted": means.wc / means.w,
                          "rows": panel.ring_wave.value_counts()}).reindex(RINGS + [REF])
    means.round(3).to_csv(OUT / "ring_means.csv")

    show = coef[coef.spec.str.match(r"[ABCS] (ignore|pre-wave|drop|unweighted|state)")]
    print("\nCoefficients (points of GOP two-party share, vs precincts >30 mi from a 2023+ site, same county):")
    print(show.pivot(index="ring", columns="spec", values="coef").reindex(RINGS).round(2).to_string())
    print("\nStandard errors:")
    print(show.pivot(index="ring", columns="spec", values="se").reindex(RINGS).round(2).to_string())
    print("\nRows per ring:", panel.ring_wave.value_counts().reindex(RINGS + [REF]).to_dict())


if __name__ == "__main__":
    main()
