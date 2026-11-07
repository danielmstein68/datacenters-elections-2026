"""Data center load relative to utility size, for the utility-bill test (decided 2026-11-07).

For each electric utility: (MW of large data centers in its territory) / (its 2022 average load
in MW = 2022 retail sales MWh / 8760). Two versions: all large sites (operating + announced), and
2023+ wave sites only.

Site MW: reported MW (low end of a range); otherwise estimated from square footage or acreage
with the same log-log fits used for the 50 MW rule; otherwise the low end of FracTracker's size
class (Large 51, Hyperscale 100, Mega campus 1,000).

Which utility serves a site is not known exactly. HIFLD territories overlap (2.4 per site on
average), so a site's MW is split equally over the retail territories that contain it (federal
and wholesale marketers left out).

Utility size: EIA-861 2022 sales / 8760; where EIA has no 2022 sales (Texas delivery utilities
such as Oncor), the HIFLD 2020 summer peak x 0.6.

Precinct exposure: the customer-weighted mean of the ratios of the territories that contain the
precinct center (more customers = more likely the precinct's utility).

Sources: HIFLD Electric Retail Service Territories (src/utilities.py; territory id = EIA utility
id); EIA-861 sales via PUDL (core_eia861__yearly_sales, public S3 copy), report year 2022.

Outputs: data/processed/utility/utility_load.csv
         data/processed/utility/{st}_2022_utility_load.csv  (precinct_id, dc_load_ratio_all, _wave)
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
RAW = ROOT / "data/raw/utility"
SALES_URL = "https://s3.us-west-2.amazonaws.com/pudl.catalyst.coop/stable/core_eia861__yearly_sales.parquet"
STATES = ["GA", "MI", "TX", "MN", "NC", "VA", "AZ", "IN", "WI"]
CLASS_MW = {"large": 51, "hyperscale": 100, "mega": 1000}


def loglog(df, col):
    m = df.mw_reported.gt(0) & df[col].gt(0)
    slope, intercept = np.polyfit(np.log(df.loc[m, col]), np.log(df.loc[m, "mw_reported"]), 1)
    return lambda x: np.exp(intercept + slope * np.log(x))


def site_mw(dc):
    sq, ac = loglog(dc, "sqft"), loglog(dc, "acres")
    mw = dc.mw_reported.copy()
    basis = pd.Series(np.where(mw.notna(), "reported", ""), index=dc.index)
    m = mw.isna() & dc.sqft.gt(0)
    mw[m], basis[m] = sq(dc.sqft[m]), "sqft"
    m = mw.isna() & dc.acres.gt(0)
    mw[m], basis[m] = ac(dc.acres[m]), "acres"
    cls = dc.sizerank.str.lower().fillna("")
    for k, v in CLASS_MW.items():
        m = mw.isna() & cls.str.contains(k)
        mw[m], basis[m] = v, "size_class"
    return mw, basis


def utility_size():
    path = RAW / "eia861_sales.parquet"
    if not path.exists():
        import urllib.request
        RAW.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SALES_URL, path)
    s = pd.read_parquet(path)
    s = s[pd.to_datetime(s.report_date).dt.year == 2022]
    size = s.groupby("utility_id_eia").agg(sales_mwh=("sales_mwh", "sum"), customers=("customers", "sum"))
    size.index = size.index.astype(str)
    size["avg_mw"] = size.sales_mwh.where(size.sales_mwh > 0) / 8760
    # Fallback (e.g. Texas delivery utilities, which report no 2022 sales): HIFLD 2020 summer peak
    # times a 0.6 load factor.
    h = pd.read_csv(RAW / "territory_size.csv", dtype={"id": str}).set_index("id")
    h = h[~h.index.duplicated()]
    size = size.join(h[["summr_peak", "customers"]].rename(columns={"customers": "customers_hifld"}), how="outer")
    fb = size.avg_mw.isna() & size.summr_peak.gt(0)
    size.loc[fb, "avg_mw"] = 0.6 * size.loc[fb, "summr_peak"]
    size["customers"] = size.customers.fillna(size.customers_hifld.where(size.customers_hifld > 0))
    size["size_basis"] = np.where(fb, "hifld_peak", np.where(size.avg_mw.notna(), "eia861_2022", "none"))
    return size


def main():
    dc = pd.read_csv(PROC / "datacenters_clean.csv")
    dc = dc[dc.treated_main].copy()
    dc["mw"], dc["mw_basis"] = site_mw(dc)
    print("site MW basis:", dc.mw_basis.value_counts().to_dict())

    su = pd.read_csv(PROC / "utility/site_utilities.csv", dtype={"utility_id": str}).dropna(subset=["utility_id"])
    # Wholesale and federal power marketers (WAPA, TVA's wholesale layer, OVEC) are not the retail
    # utility a site buys from; leave them out of the split.
    su = su[~su.utility_type.isin(["FEDERAL", "MUNICIPAL MKTG AUTHORITY", "NOT AVAILABLE"])]
    su = su.merge(dc[["site_id", "mw"]], on="site_id")
    size = utility_size()
    wholesale = size.index[size.customers.lt(100)]  # e.g. OVEC: one customer, not a retail utility
    su = su[~su.utility_id.isin(wholesale)]
    su["mw_split"] = su.mw / su.groupby("site_id").utility_id.transform("count")
    load = pd.DataFrame({
        "dc_mw_all": su.groupby("utility_id").mw_split.sum(),
        "dc_mw_wave": su[su.wave == "2023+"].groupby("utility_id").mw_split.sum(),
    }).fillna(0)
    load = load.join(size[["avg_mw", "customers", "size_basis"]], how="left")
    load["ratio_all"] = load.dc_mw_all / load.avg_mw
    load["ratio_wave"] = load.dc_mw_wave / load.avg_mw
    names = su.drop_duplicates("utility_id").set_index("utility_id")[["utility_name", "utility_type"]]
    load = names.join(load, how="right").sort_values("dc_mw_all", ascending=False)
    load.round(4).to_csv(PROC / "utility/utility_load.csv")
    study = su[su.target_state].utility_id.unique()
    print(f"utilities with a large site: {len(load)}, no size data: {load.avg_mw.isna().sum()} "
          f"(in study states: {load.loc[load.index.isin(study), 'avg_mw'].isna().sum()} of {len(study)}, "
          f"{load.loc[load.index.isin(study) & load.avg_mw.isna(), 'dc_mw_all'].sum():,.0f} MW)")
    print(load.head(15)[["utility_name", "dc_mw_all", "avg_mw", "ratio_all", "ratio_wave"]].round(2).to_string())

    summary = []
    for st in STATES:
        h = pd.read_csv(PROC / f"utility/{st.lower()}_2022_utilities.csv", dtype={"utility_id": str})
        h = h[h.utility_id.notna() & ~h.utility_type.isin(["FEDERAL", "MUNICIPAL MKTG AUTHORITY", "NOT AVAILABLE"])
              & ~h.utility_id.isin(wholesale)]
        h = h.merge(size[["customers"]], left_on="utility_id", right_index=True, how="left")
        h = h.merge(load[["ratio_all", "ratio_wave"]], left_on="utility_id", right_index=True, how="left")
        has_site = h.utility_id.isin(load.index)
        h.loc[~has_site, ["ratio_all", "ratio_wave"]] = 0  # no large site in this territory
        h = h[h.ratio_all.notna()]  # territory has a site but no size data: left out
        h["w"] = h.customers.fillna(0).clip(lower=1)
        p = h.groupby("precinct_id").apply(lambda g: pd.Series({
            "dc_load_ratio_all": np.average(g.ratio_all, weights=g.w),
            "dc_load_ratio_wave": np.average(g.ratio_wave, weights=g.w)}), include_groups=False).reset_index()
        p.round(5).to_csv(PROC / f"utility/{st.lower()}_2022_utility_load.csv", index=False)
        q = p.dc_load_ratio_all.quantile([0.1, 0.5, 0.9]).round(3).tolist()
        summary.append({"state": st, "precincts": len(p), "p10": q[0], "median": q[1], "p90": q[2],
                         "share_zero": (p.dc_load_ratio_all == 0).mean().round(3)})
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()
