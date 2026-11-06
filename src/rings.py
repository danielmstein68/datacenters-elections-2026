"""Assign every precinct to a distance ring around the nearest large data center.

Distance is great-circle miles from the precinct's population-weighted center
(data/processed/{vest,s22,nc}/{st}_{year}_centers.csv) to the nearest site in
data/processed/datacenters_clean.csv. Sites in every state count, so a site just
across a state line still counts as near.

Three distances are kept per precinct:
  any      nearest site with treated_main (50 MW+, not cancelled, dated)
  wave     nearest treated_main site in the 2023+ wave
  prewave  nearest treated_main site that was operating before the wave

Rings (miles, fixed in the design): 0-1, 1-2, 2-5, 5-10, 10-20, 20-30, 30+.
The 20-30 ring is the check ring and should show no effect; with it split out,
the comparison group is precincts more than 30 miles away.

Outputs: data/processed/rings/{st}_{year}_rings.csv
"""

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data/processed"
OUT = PROC / "rings"

BREAKS = [0, 1, 2, 5, 10, 20, 30, np.inf]
LABELS = ["0-1", "1-2", "2-5", "5-10", "10-20", "20-30", "30+"]
EARTH_MI = 3958.8

CENTERS = {
    2018: {st: PROC / f"vest/{st}_2018_centers.csv" for st in ["ga", "mi", "tx", "mn", "va", "az", "in", "wi"]},
    2022: {st: PROC / f"s22/{st}_2022_centers.csv" for st in ["ga", "mi", "tx", "mn", "va", "az", "in", "wi"]},
}
for y in (2018, 2022):
    CENTERS[y]["nc"] = PROC / f"nc/nc_{y}_centers.csv"


def nearest(lat, lon, sites):
    """Distance in miles and site_id of the nearest site, for each point."""
    if sites.empty:
        return np.full(len(lat), np.inf), np.full(len(lat), None)
    p1, l1 = np.radians(lat)[:, None], np.radians(lon)[:, None]
    p2, l2 = np.radians(sites.lat.to_numpy())[None, :], np.radians(sites.lon.to_numpy())[None, :]
    a = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin((l2 - l1) / 2) ** 2
    d = 2 * EARTH_MI * np.arcsin(np.sqrt(a))
    i = d.argmin(axis=1)
    return d[np.arange(len(lat)), i], sites.site_id.to_numpy()[i]


def ring(d):
    return pd.cut(d, BREAKS, right=False, labels=LABELS).astype(str)


def main():
    dc = pd.read_csv(PROC / "datacenters_clean.csv")
    dc = dc[dc.treated_main & dc.lat.notna() & dc.lon.notna()]
    groups = {"any": dc, "wave": dc[dc.wave == "2023+"], "prewave": dc[dc.wave == "pre-wave"]}
    print(f"sites: any {len(dc)}, wave {len(groups['wave'])}, prewave {len(groups['prewave'])}")

    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    for year, files in CENTERS.items():
        for st, path in files.items():
            if not path.exists():
                print(f"{st.upper()} {year}: no centers file, skipped")
                continue
            c = pd.read_csv(path)
            out = c[["precinct_id", "pop20", "lat", "lon", "center_method"]].copy()
            for g, sites in groups.items():
                d, sid = nearest(c.lat.to_numpy(), c.lon.to_numpy(), sites)
                out[f"dist_{g}"] = d.round(3)
                out[f"site_{g}"] = sid
                out[f"ring_{g}"] = ring(d)
            out.to_csv(OUT / f"{st}_{year}_rings.csv", index=False)
            n = out.ring_any.value_counts().reindex(LABELS, fill_value=0)
            summary.append({"state": st.upper(), "year": year, "precincts": len(out), **n.to_dict()})
    s = pd.DataFrame(summary)
    s.to_csv(OUT / "ring_counts.csv", index=False)
    print("Precincts by ring (nearest of any large site):")
    print(s.to_string(index=False))


if __name__ == "__main__":
    main()
