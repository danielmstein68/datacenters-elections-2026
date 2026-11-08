"""2018-to-2022 placebo for the utility-bill test: three ways to run it (chosen 2026-11-07:
test all three, keep the ones that pass).

Exposure: log(1 + data center load ratio) of the precinct's utilities, counting only 2023+ wave
sites (src/utility_load.py, dc_load_ratio_wave). Those sites did not exist in 2018-2022, so a
real bill effect cannot show up here; any slope is bias in the comparison.

Outcome and panel: the ring placebo's panel (data/processed/placebo/panel.csv): change in GOP
two-party share 2018 to 2022 on 2022 precincts, weighted by 2022 two-party votes, standard
errors clustered by county.

Versions:
  state    state-by-office effects + ring dummies (nearest 2023+ site) + 2018 GOP share
           and its square
  county   county-by-office effects + ring dummies
  border   precincts whose main utility (the territory with most customers containing the
           center) differs from that of another precinct within 3 miles; effects for each
           utility pair x county x office, + ring dummies. Compares neighbors across a
           utility line.

Output: data/processed/placebo/utility_placebo.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

from placebo import RINGS, ols_cluster

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
STATES = ["ga", "mi", "tx", "mn", "nc", "va", "az", "in", "wi"]
BORDER_MI = 3.0
EARTH_MI = 3958.8


def main_utility(u):
    s = pd.read_parquet(ROOT / "data/raw/utility/eia861_sales.parquet",
                        columns=["utility_id_eia", "report_date", "customers"])
    s = s[pd.to_datetime(s.report_date).dt.year == 2022]
    cust = s.groupby(s.utility_id_eia.astype(str)).customers.sum()
    u = u.dropna(subset=["utility_id"]).copy()
    u = u[~u.utility_type.isin(["FEDERAL", "MUNICIPAL MKTG AUTHORITY", "NOT AVAILABLE"])]
    u["cust"] = u.utility_id.map(cust).fillna(0)
    u = u.sort_values("cust").drop_duplicates("precinct_id", keep="last")
    return u.set_index("precinct_id").utility_id


def border_pairs(c):
    """For each precinct, the main utility of the nearest precinct within BORDER_MI that has a
    different main utility (or none)."""
    lat, lon = np.radians(c.lat.to_numpy()), np.radians(c.lon.to_numpy())
    util = c.main_util.to_numpy()
    other = np.full(len(c), None, dtype=object)
    for i in range(len(c)):
        d = 2 * EARTH_MI * np.arcsin(np.sqrt(np.sin((lat - lat[i]) / 2) ** 2 +
                                             np.cos(lat[i]) * np.cos(lat) * np.sin((lon - lon[i]) / 2) ** 2))
        m = (d < BORDER_MI) & (util != util[i])
        if m.any():
            other[i] = util[np.where(m)[0][d[m].argmin()]]
    return other


def fit(d, fe, extra, label):
    X = pd.DataFrame({"exposure": d.exposure}, index=d.index)
    for r in RINGS:
        X[f"ring_{r}"] = (d.ring_wave == r).astype(float)
    for e in extra:
        X[e] = d[e]
    X = pd.concat([X, pd.get_dummies(fe, dtype=float)], axis=1)
    b, se, G = ols_cluster(d.change.to_numpy(float), X.to_numpy(float), d.tp_22.to_numpy(float),
                           d.county.to_numpy())
    sd = np.sqrt(np.cov(d.exposure, aweights=d.tp_22))
    return {"version": label, "coef_per_log_unit": b[0], "se": se[0], "t": b[0] / se[0],
            "effect_of_1sd": b[0] * sd, "rows": len(d), "clusters": G}


def main():
    panel = pd.read_csv(PROC / "placebo/panel.csv")
    parts = []
    for st in STATES:
        load = pd.read_csv(PROC / f"utility/{st}_2022_utility_load.csv")
        u = pd.read_csv(PROC / f"utility/{st}_2022_utilities.csv", dtype={"utility_id": str})
        c = pd.read_csv(PROC / f"rings/{st}_2022_rings.csv")[["precinct_id", "lat", "lon"]]
        c["main_util"] = c.precinct_id.map(main_utility(u))
        c = c.dropna(subset=["main_util"]).reset_index(drop=True)
        c["other_util"] = border_pairs(c)
        parts.append(c.merge(load, on="precinct_id", how="left"))
    ex = pd.concat(parts, ignore_index=True)
    p = panel.merge(ex, on="precinct_id", how="inner").dropna(subset=["dc_load_ratio_wave"])
    p["exposure"] = np.log1p(p.dc_load_ratio_wave)
    p["r18"], p["r18sq"] = p.rshare_18, p.rshare_18 ** 2 / 100

    res = [
        fit(p, p.state + ":" + p.office, ["r18", "r18sq"], "state (+2018 vote)"),
        fit(p, p.county + ":" + p.office, [], "county"),
    ]
    b = p[p.other_util.notna()].copy()
    pair = np.where(b.main_util < b.other_util, b.main_util + "|" + b.other_util, b.other_util + "|" + b.main_util)
    b["pair"] = pair
    res.append(fit(b, b.pair + ":" + b.county + ":" + b.office, [], f"border pairs (<{BORDER_MI:g} mi)"))

    # Spread of exposure that each version actually uses (after its fixed effects).
    for r, (d, fe) in zip(res, [(p, p.state + ":" + p.office), (p, p.county + ":" + p.office),
                                (b, b.pair + ":" + b.county + ":" + b.office)]):
        dm = d.exposure - d.groupby(fe).exposure.transform("mean")
        r["usable_sd_of_exposure"] = np.sqrt(np.average(dm ** 2, weights=d.tp_22))
    out = pd.DataFrame(res)
    out.round(4).to_csv(PROC / "placebo/utility_placebo.csv", index=False)
    print(out.round(3).to_string(index=False))
    print("\nbyState border rows:", b.groupby("state").size().to_dict())


if __name__ == "__main__":
    main()
