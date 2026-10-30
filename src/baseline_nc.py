"""Download and tidy North Carolina precinct results for the 2018 and 2022 general elections.

NC posts statewide precinct files (results_pct_YYYYMMDD.zip). About 40 of 100 counties report
early and absentee votes in county-wide "precincts" (ABSENTEE, ONE STOP, PROVISIONAL, ...) that
have no geography. Those rows are kept and flagged, and an allocated vote count is added that
spreads each county's pooled votes across its real precincts in proportion to each candidate's
real-precinct votes (the approach VEST uses).

Outputs: data/processed/nc_precinct_results.csv
         columns: year, county, precinct, office, district, contest, candidate, party,
                  votes_real (geographic precinct votes only), votes_allocated
"""

import io
import re
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/nc"
OUT = ROOT / "data/processed/nc_precinct_results.csv"

ELECTIONS = {2018: "2018_11_06", 2022: "2022_11_08"}
URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{d}/results_pct_{s}.zip"


def office_of(contest):
    if contest == "US SENATE":
        return "senate", None
    m = re.fullmatch(r"US HOUSE OF REPRESENTATIVES DISTRICT (\d+)", contest)
    if m:
        return "house", int(m.group(1))
    if contest == "NC GOVERNOR":
        return "governor", None
    if contest.startswith("NC SUPREME COURT"):
        return "nc_supreme_court", contest.rsplit(" ", 1)[-1]
    return None, None


def download(year):
    d = ELECTIONS[year]
    path = RAW / f"results_pct_{d.replace('_', '')}.zip"
    if not path.exists():
        RAW.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL.format(d=d, s=d.replace("_", "")), path)
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.endswith(".txt"))
        return pd.read_csv(io.BytesIO(z.read(name)), sep="\t", dtype=str)


def tidy(year):
    r = download(year)
    r["office"], r["district"] = zip(*r["Contest Name"].map(office_of))
    r = r[r["office"].notna()].copy()
    df = pd.DataFrame(
        {
            "year": year,
            "county": r["County"].str.strip(),
            "precinct": r["Precinct"].str.strip(),
            "real_precinct": r["Real Precinct"].str.strip().eq("Y"),
            "office": r["office"],
            "district": r["district"],
            "contest": r["Contest Name"].str.strip(),
            "candidate": r["Choice"].str.strip(),
            "party": r["Choice Party"].str.strip().replace("", pd.NA),
            "votes": pd.to_numeric(r["Total Votes"], errors="coerce").fillna(0).astype(int),
        }
    )

    keys = ["county", "contest", "candidate"]
    real = df[df.real_precinct].copy()
    pooled = df[~df.real_precinct].groupby(keys).votes.sum().rename("pooled")
    real_total = real.groupby(keys).votes.sum().rename("real_total")
    real = real.join(pooled, on=keys).join(real_total, on=keys)
    real["pooled"] = real["pooled"].fillna(0)
    share = (real["votes"] / real["real_total"]).where(real["real_total"] > 0, 0)
    real["votes_allocated"] = real["votes"] + real["pooled"] * share

    # Pooled votes for a candidate with no real-precinct votes in that county can't be placed.
    lost = pooled.sum() - (real["votes_allocated"] - real["votes"]).sum()
    print(f"{year}: {real[['county', 'precinct']].drop_duplicates().shape[0]} precincts, "
          f"{pooled.sum():,} pooled votes allocated, {lost:,.0f} unplaceable")

    return real.rename(columns={"votes": "votes_real"})[
        ["year", "county", "precinct", "office", "district", "contest", "candidate", "party",
         "votes_real", "votes_allocated"]
    ]


def main():
    out = pd.concat([tidy(y) for y in ELECTIONS], ignore_index=True)
    out["votes_allocated"] = out["votes_allocated"].round(2)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT, index=False)
    print(out.groupby(["year", "office"]).agg(rows=("votes_real", "size"),
                                              votes_real=("votes_real", "sum"),
                                              votes_allocated=("votes_allocated", "sum")).round(0))


if __name__ == "__main__":
    main()
