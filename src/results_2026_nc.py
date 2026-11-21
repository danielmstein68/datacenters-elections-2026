"""North Carolina 2026 precinct results on the State Board's Aug 2026 precinct map.

Uses the State Board's statewide precinct file (results_pct_20261103.zip; unofficial until the
county canvass, Nov 13, then re-downloaded) with the same tidying and pooled-vote allocation as
2018/2022 (src/baseline_nc.py), then joins on county + precinct code (src/baseline_nc_geo.py).

Output: data/processed/nc/nc_2026_results.csv
"""

from pathlib import Path

import pandas as pd

import baseline_nc
from baseline_nc_geo import place_results
from maps_2026 import load_nc

ROOT = Path(__file__).resolve().parents[1]


def main():
    baseline_nc.ELECTIONS[2026] = "2026_11_03"
    res = baseline_nc.tidy(2026)
    res["party"] = res.party.map({"REP": "R", "DEM": "D"}).fillna("O")
    out, report = place_results(res, load_nc(), 2026)
    out.to_csv(ROOT / "data/processed/nc/nc_2026_results.csv", index=False)
    print(report.to_string(index=False))
    print(out.groupby("office").precinct_id.nunique().to_dict())


if __name__ == "__main__":
    main()
