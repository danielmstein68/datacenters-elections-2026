"""Clean the FracTracker data center export and apply the pre-registered size and dating rules.

Inputs:  data/raw/fractracker_datacenters_2026-10-30.csv
Outputs: data/processed/datacenters_clean.csv
         data/processed/size_rule_fit.txt
         data/hand/expansion_dates.csv     (to fill in by hand)
         data/hand/spotcheck_sample.csv    (to fill in by hand)

Rules (decided 2026-10-30, see README):
  Size   - 50 MW+ if reported MW >= 50 or FracTracker's size class is Large/Hyperscale/Mega.
           If size is unknown, estimate from building square footage, falling back to acreage.
           The cutoffs are frozen below; the fit is re-run only as a check.
           Sites with no size information at all are excluded from the main analysis.
  Dating - Proposed, pre-proposal, approved/permitted/under construction, suspended -> 2023+ wave.
           Operating -> pre-wave, unless an expected online year or a dated source says 2023 or later.
           Expanding -> dated by hand from the expansion announcement.
           Cancelled -> kept in the file but not treated in the main analysis.
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/fractracker_datacenters_2026-10-30.csv"
OUT = ROOT / "data/processed"
HAND = ROOT / "data/hand"

# IA and NE dropped, AZ, IN and WI added on 2026-11-06 (see README Deviations).
TARGET_STATES = ["GA", "MI", "TX", "MN", "NC", "VA", "AZ", "IN", "WI"]
MW_CUTOFF = 50

# Frozen from the 2026-10-30 fit of log(MW) on log(size) among sites reporting both.
SQFT_CUTOFF = 267_373
ACRES_CUTOFF = 19

WAVE_STATUSES = {
    "Proposed",
    "Pre-proposal",
    "Approved/Permitted/Under construction",
    "Suspended",
}
SPOTCHECK_N = 50
SEED = 20261030


def to_number(s):
    """Pull the first number out of messy text like '1,200 MW', '1.3M' or '3 million'.

    For a range like '100-200' this takes the low end.
    """
    parts = s.astype("string").str.replace(",", "", regex=False).str.extract(
        r"([\d.]+)\s*(m(?:illion)?\b)?", flags=re.IGNORECASE
    )
    value = pd.to_numeric(parts[0], errors="coerce")
    return value.where(parts[1].isna(), value * 1_000_000)


def earliest_source_date(row):
    """Earliest date that appears in a source URL path, e.g. /2025/11/19/."""
    dates = []
    for v in row:
        if not isinstance(v, str):
            continue
        for y, m, d in re.findall(r"/(20[12]\d)/(\d{1,2})/(\d{1,2})/", v):
            dates.append(f"{y}-{int(m):02d}-{int(d):02d}")
        for y, m in re.findall(r"/(20[12]\d)[/-](\d{2})/", v):
            dates.append(f"{y}-{m}-01")
    return min(dates) if dates else None


def fit_cutoff(df, col):
    """Log-log fit of MW on a size column; returns (n, r2, size at 50 MW)."""
    m = df["mw_reported"].gt(0) & df[col].gt(0)
    x, y = np.log(df.loc[m, col]), np.log(df.loc[m, "mw_reported"])
    slope, intercept = np.polyfit(x, y, 1)
    r2 = np.corrcoef(x, y)[0, 1] ** 2
    return int(m.sum()), r2, float(np.exp((np.log(MW_CUTOFF) - intercept) / slope))


def size_rule(df):
    size_class = df["sizerank"].str.lower().str.strip()
    known_big = size_class.str.contains("hyperscale|large|mega", na=False)
    known_small = size_class.str.contains("small|medium", na=False)

    df["size_basis"] = "none"
    df["mw50_plus"] = pd.NA

    has_acres = df["acres"].notna()
    df.loc[has_acres, "size_basis"] = "acres"
    df.loc[has_acres, "mw50_plus"] = df.loc[has_acres, "acres"] >= ACRES_CUTOFF

    has_sqft = df["sqft"].notna()
    df.loc[has_sqft, "size_basis"] = "sqft"
    df.loc[has_sqft, "mw50_plus"] = df.loc[has_sqft, "sqft"] >= SQFT_CUTOFF

    df.loc[known_big | known_small, "size_basis"] = "size_class"
    df.loc[known_big, "mw50_plus"] = True
    df.loc[known_small, "mw50_plus"] = False

    has_mw = df["mw_reported"].notna()
    df.loc[has_mw, "size_basis"] = "reported_mw"
    df.loc[has_mw, "mw50_plus"] = df.loc[has_mw, "mw_reported"] >= MW_CUTOFF
    df["mw50_plus"] = df["mw50_plus"].astype("boolean")
    return df


def dating_rule(df):
    late_online = df["expected_online_year"].ge(2023)
    late_source = pd.to_datetime(df["earliest_source_date"], errors="coerce").dt.year.ge(2023)

    df["wave"] = pd.NA
    df["wave_basis"] = pd.NA
    df.loc[df["status"].isin(WAVE_STATUSES), ["wave", "wave_basis"]] = ["2023+", "status"]

    op = df["status"].eq("Operating")
    df.loc[op, ["wave", "wave_basis"]] = ["pre-wave", "status"]
    df.loc[op & late_online, ["wave", "wave_basis"]] = ["2023+", "expected_online_year"]
    df.loc[op & ~late_online & late_source, ["wave", "wave_basis"]] = ["2023+", "source_date"]

    df.loc[df["status"].eq("Expanding"), ["wave", "wave_basis"]] = ["needs_hand_date", "expansion"]
    df.loc[df["status"].eq("Cancelled"), ["wave", "wave_basis"]] = ["cancelled", "status"]
    return df


def apply_expansion_dates(df):
    """Date expanding sites from the hand-filled sheet (2023+ if the expansion went public in 2023 or later)."""
    path = HAND / "expansion_dates.csv"
    if not path.exists():
        return df
    hand = pd.read_csv(path, dtype=str).dropna(subset=["expansion_announced"]).set_index("site_id")
    year = pd.to_numeric(hand.expansion_announced.str[:4], errors="coerce")
    m = df["site_id"].isin(year.dropna().index) & df["wave"].eq("needs_hand_date")
    yr = df.loc[m, "site_id"].map(year)
    df.loc[m, "wave"] = yr.ge(2023).map({True: "2023+", False: "pre-wave"})
    df.loc[m, "wave_basis"] = "hand_expansion_date"
    return df


def main():
    raw = pd.read_csv(RAW, dtype=str)
    df = pd.DataFrame(
        {
            "site_id": [f"FT{i:05d}" for i in range(1, len(raw) + 1)],
            "facility_name": raw["facility_name"],
            "operator_name": raw["operator_name"],
            "state": raw["state"].str.strip().str.upper(),
            "county": raw["county"],
            "city": raw["city"],
            "lat": pd.to_numeric(raw["lat"], errors="coerce"),
            "lon": pd.to_numeric(raw["long"], errors="coerce"),
            "location_confidence": raw["location_confidence"].str.strip().str.capitalize(),
            "status": raw["status"].str.strip(),
            "sizerank": raw["sizerank"],
            "mw_reported": to_number(raw["mw"]),
            "sqft": to_number(raw["facility_size_sqft"]),
            "acres": to_number(raw["property_size_acres"]),
            "expected_online_year": to_number(raw["expected_date_online"]),
            "community_pushback": raw["community_pushback"].str.strip().str.lower().eq("yes"),
            "fractracker_date_created": raw["date_created"],
        }
    )
    df["earliest_source_date"] = raw.filter(like="info_source_").apply(earliest_source_date, axis=1)
    df["source_1"] = raw["info_source_1"]
    df["target_state"] = df["state"].isin(TARGET_STATES)

    df = size_rule(df)
    df = dating_rule(df)
    df = apply_expansion_dates(df)

    # Main-analysis treatment: large, not cancelled, dated. Expansions join once hand-dated.
    df["treated_main"] = df["mw50_plus"].fillna(False) & df["wave"].isin(["2023+", "pre-wave"])

    OUT.mkdir(parents=True, exist_ok=True)
    HAND.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "datacenters_clean.csv", index=False)

    # Re-run the size fit as a check against the frozen cutoffs.
    lines = [f"Size rule check, data file {RAW.name}"]
    for col, frozen in [("sqft", SQFT_CUTOFF), ("acres", ACRES_CUTOFF)]:
        n, r2, cut = fit_cutoff(df, col)
        lines.append(f"{col}: n={n}, R2={r2:.2f}, 50 MW at {cut:,.0f} (frozen: {frozen:,})")
    (OUT / "size_rule_fit.txt").write_text("\n".join(lines) + "\n")

    big = df[df["mw50_plus"].fillna(False)]
    exp = big[big["wave"].eq("needs_hand_date")]
    exp_cols = ["site_id", "facility_name", "operator_name", "state", "county", "target_state", "source_1"]
    hand_exp = exp[exp_cols].assign(expansion_announced="", source_url="", notes="")
    if not (HAND / "expansion_dates.csv").exists():  # never overwrite hand-filled sheets
        hand_exp.sort_values(["target_state", "state"], ascending=[False, True]).to_csv(
            HAND / "expansion_dates.csv", index=False
        )

    pool = big[big["target_state"] & big["wave"].isin(["2023+", "pre-wave"])]
    sample = pool.sample(n=min(SPOTCHECK_N, len(pool)), random_state=SEED)
    if not (HAND / "spotcheck_sample.csv").exists():
        sample[exp_cols + ["status", "wave", "wave_basis"]].assign(
            first_public_date="", source_url="", rule_correct="", notes=""
        ).to_csv(HAND / "spotcheck_sample.csv", index=False)

    tgt = df[df["target_state"]]
    print("\n".join(lines))
    print(f"\nAll sites: {len(df)}; target states: {len(tgt)}")
    print("\nSize basis (all sites):")
    print(df["size_basis"].value_counts().to_string())
    print("\nTarget states, 50 MW+ by wave:")
    print(pd.crosstab(tgt.loc[tgt.mw50_plus.fillna(False), "state"], tgt.loc[tgt.mw50_plus.fillna(False), "wave"], margins=True).to_string())
    print(f"\nTreated in main analysis (target states): {int(tgt.treated_main.sum())}")
    print(f"Expansions to hand-date: {len(hand_exp)} ({int(hand_exp.target_state.sum())} in target states)")
    print(f"Spot-check sample: {len(sample)}")


if __name__ == "__main__":
    main()
