# Data centers and the 2026 vote

Do voters who live near large data centers vote differently in the 2026 midterms? This study is modeled on the wind turbine literature (Stokes 2016; Otteni and Weisskircher 2022). It compares how precincts near data centers shifted from 2022 to 2026 against how precincts farther away shifted.

The design below was fixed before Election Day (Nov 3, 2026). Changes after that date are listed under **Deviations**.

## Design

**Hypotheses.** Voters near data centers punish one of the following:

1. Local incumbents
2. Statewide incumbents
3. The Republican Party

Hypotheses 2 and 3 are distinguished by the party of the office holder. If the shift changes sign with the incumbent's party, that supports incumbency. A consistent shift against the GOP supports partisanship.

**Offices.** House, Senate and governor, compared within the same precinct where the races share a ballot. Uncontested House races are dropped. House races in TX and NC are also dropped because their districts were redrawn mid-decade; both states stay in for Senate and governor.

**Treatment.** Data centers of 50 MW or more that are operating, announced or under construction. Projects that went public in 2023 or later are flagged as the AI wave. See *Data center rules* below.

**Distance.** Each precinct is assigned to a distance ring based on the nearest data center: 0–1, 1–2, 2–5, 5–10 and 10–20 miles, compared against precincts more than 20 miles away in the same state. A 20–30 mile ring should show no effect. Distance is measured from the population-weighted center of each precinct.

**Utility-bill test.** This checks whether precincts served by the same electric utility as a large data center shift regardless of distance. It separates a backyard effect from an electric-bill effect.

**Identification.** Difference-in-differences on the precinct-level change in vote from 2022 to 2026. A placebo uses the 2018-to-2022 change: precincts near 2023+ data centers should show no differential shift before those projects existed.

**Votes not tied to a precinct.** Some counties report early, absentee or provisional ballots as one county-wide total. These are spread across the county's precincts in proportion to each candidate's precinct votes, following VEST. Counts from geographic precincts only are kept for a robustness check.

**Precinct geography.** For GA, MI, TX, MN, VA, AZ, IN and WI, 2018 precinct results and boundaries come from VEST (Harvard Dataverse, `src/baseline_vest.py --years 2018`). VEST's 2018 files mostly carry governor and Senate only, not House. Each precinct is matched to 2020 census blocks by block internal point. Block populations give the population-weighted precinct center and a precinct-to-block-group crosswalk. Precincts with no 2020 residents use a point inside the polygon. 

**2022 precincts (free sources).** VEST's 2022 files are sold by subscription, so 2022 is built from free sources by `src/baseline_2022.py` (decided 2026-10-31). TX uses Texas Legislative Council VTD results and maps (exact keys). MN uses MEDSL results on LCC 2022 precinct maps, MI uses OpenElections results on the state's 2022 precinct map. WI uses the Legislative Technology Services Bureau's 2022 wards, which come with 2022 results attached (exact keys). AZ uses MEDSL results on Maricopa's 2022 precinct map, Pima's 2024 map (its precinct numbers match 2022) and 2020 census VTDs elsewhere. IN uses MEDSL results on IndianaMap's 2023 precinct map; MEDSL has 2022 Indiana precinct results for only 39 of 92 counties, so the other counties have no 2022 baseline. GA and VA have no free 2022 statewide precinct map found yet, so MEDSL results are matched to 2020 census voting districts. Results name precincts but carry no map key, so names are matched within each county in steps (exact, reordered words, ward dropped, split precincts merged, code, fuzzy); votes left unmatched are spread over the county's matched precincts like county-wide absentee votes. Statewide totals from MEDSL and TLC match the official counts; OpenElections MI is within 0.1%. Votes in a county where nothing matched cannot be placed and drop out (the "lost" share below). Match quality is in `data/processed/s22/match_report.csv`:

| State | Map | Votes matched to a map precinct | Usable? |
|---|---|---|---|
| TX | 2022 VTDs | 100% | yes |
| MN | 2022 precincts | 99.4% | yes |
| MI | 2022 precincts | 96.2% | yes |
| GA | 2020 VTDs | 85.7%; 3.9% of votes lost where whole counties failed to match | with care |
| WI | 2022 wards | 100% | yes |
| AZ | Maricopa 2022, Pima 2024, 2020 VTDs elsewhere | 98.7% | yes |
| IN | 2023 precincts | 96.5% of the votes MEDSL has, but only 39 of 92 counties are covered (about 42% of statewide votes). Senate and House only in 2022, House only in 2026 | partly |
| VA | 2020 VTDs | 62% (93% of votes cast in precincts; the rest is central absentee, spread by county). Only House races in 2022; VA was not redistricted, so they are kept | with care |


**North Carolina placebo.** NC had no Senate race in 2018, so its 2018-to-2022 placebo uses the NC Supreme Court races, which are partisan and statewide in both years.

**North Carolina geography.** NC results are placed on the State Board of Elections' statewide precinct maps by county and precinct code (`src/baseline_nc_geo.py`): the May 2017 map for 2018 (all 2,706 precincts match) and the Aug 2022 map for 2022 (all but 3 precincts match; their 0.04% of votes are spread over the county). Centers and block-group crosswalks are built the same way as the other states.

**Distance rings (`src/rings.py`).** Each 2018 and 2022 precinct center gets the distance in miles to the nearest large site of any vintage, the nearest 2023+ wave site and the nearest pre-wave site, with sites in every state counted (a site across a state line still counts). Output: `data/processed/rings/`. With the 20–30 mile check ring split out, the comparison group is precincts more than 30 miles away.

**2018-to-2022 placebo (`src/placebo.py`).** 2018 votes are moved onto 2022 precincts through block groups. The outcome is the change in GOP share of the two-party vote for each office on the ballot in both years (governor and/or Senate; NC Supreme Court). VA has no such office and is left out; IN covers only the 39 counties with 2022 precinct results. Precincts are compared only with precincts in the same county (county-by-office effects), weighted by 2022 two-party votes, with standard errors clustered by county. Result (Nov 6): within 2 miles of a 2023+ site the 2018-to-2022 shift is close to zero (-0.4 points, SE 0.5 to 0.8). From 2 to 30 miles, precincts moved 0.8 to 1.0 points less Republican than precincts more than 30 miles away in the same county (SE 0.3 to 0.45), with no gradient by distance. Handling of pre-wave sites (ignore them, add their rings, or drop precincts within 20 miles of one) barely changes this. Full table: `data/processed/placebo/coefficients.csv`.

**Utility-bill test (`src/utilities.py`, `src/utility_load.py`).** Decided 2026-11-07: exposure is each utility's large data center load relative to its size, because 91 to 100% of precincts in most states sit in a territory that holds some large site. For each utility, the MW of large sites in its HIFLD retail territory is divided by its 2022 average load (EIA-861 sales / 8760; HIFLD summer peak x 0.6 where EIA has no 2022 sales, as for Texas delivery utilities). Site MW is reported MW, else estimated from square footage or acreage with the 50 MW rule's fits. Territories overlap, so each site's MW is split equally over the retail territories containing it, leaving out federal and wholesale marketers. A precinct's exposure is the customer-weighted mean ratio of the territories containing its center. Within-state spread is small in GA and VA, where one large utility dominates.

**States and timing.** The precinct analysis covers GA, MI, TX, MN, NC, VA, AZ, IN and WI, using results finalized in county canvasses by Nov 21, 2026. The governor-vs-Senate comparison uses GA, MI, TX and MN (AZ and WI elect a governor in 2026 but no senator). A county-level version covers all states. Everything is re-run on certified results.

## Data center rules

Source: FracTracker Alliance's data center tracker, exported Oct 30, 2026 (1,733 sites).

**Size.** A site counts as 50 MW+ when any of these apply, checked in order:

1. Its reported MW is 50 or more. For a range, the low end is used.
2. FracTracker's size class is Large, Hyperscale or Mega campus.
3. Its building is at least 267,373 sq ft.
4. With no square footage listed, its property is at least 19 acres.

The two cutoffs come from a log-log fit of MW on size among sites that report both. For square footage: n = 420, R² = 0.70. For acreage: n = 389, R² = 0.58. The cutoffs are frozen in `src/prep_datacenters.py`. Sites with no size information (197) are excluded from the main analysis and counted as treated in a robustness check.

**Dating.** These rules classify whether a project went public before or after late 2022:

| FracTracker status | Classified as |
| --- | --- |
| Proposed, pre-proposal, approved/permitted/under construction, suspended | 2023+ wave |
| Operating | Pre-wave, unless an expected online year or a dated source is 2023 or later |
| Expanding | Dated by hand from the expansion announcement (`data/hand/expansion_dates.csv`) |
| Cancelled | Kept in the file, not treated in the main analysis |

A fixed random sample of 50 large sites in the target states (seed 20261030) is checked by hand against news coverage to measure how often the rule is wrong (`data/hand/spotcheck_sample.csv`). Result (Nov 7, research pass, 48 of 50 dated): the rule was wrong for 7 of 48 (15%), 6 of 44 labeled 2023+ (projects public in 2020-22) and 1 of 4 labeled pre-wave. Expanding sites are dated from `data/hand/expansion_dates.csv` (45 of 52 dated; 32 of the 38 large target-state sites); the 6 still undated stay out of the main analysis.

## Repository layout

```
data/raw/        FracTracker export and its README, as received
data/processed/  datacenters_clean.csv, size_rule_fit.txt
data/hand/       sheets filled in by hand
src/             scripts
```

To rebuild the processed files, run `python src/prep_datacenters.py` (requires `pandas` and `numpy`).

## Data citation

Data provided by FracTracker Alliance (2026). Accessed via https://www.fractracker.org/data-centers/. The data are used under FracTracker's terms of service, which are included in `data/raw/fractracker_README_2026-10-30.txt`.

## Deviations

- **2026-11-06: Iowa and Nebraska dropped.** No free 2022 precinct map was found for either state. Matched to 2020 census voting districts, only 27% (IA) and 45% (NE) of 2022 votes found a precinct, too few for a precinct-level comparison. VEST's 2022 maps are paid.
- **2026-11-06: Arizona, Indiana and Wisconsin added** to replace them. All three certify county results by Nov 21 and have free 2022 precinct data (see *2022 precincts*). The data center file's `target_state` flag was updated to match.
- **2026-11-06: Within-county comparison.** The planned comparison against precincts more than 20 to 30 miles away in the same state failed the 2018-to-2022 placebo: every ring out to 30 miles moved 2 to 3 points less Republican, a flat gap that tracks metro versus rural trends. Precincts are now compared only with precincts in the same county (county-by-office effects), for the placebo and the 2026 analysis. The state comparison is still reported.
- **2026-11-07: Utility-bill test redefined** as data center load relative to utility size (see *Utility-bill test*), since nearly all precincts share a utility with some large site.
- **2026-11-07: Senate baseline.** Where a state had no 2022 Senate race (MI, MN, TX), the 2026 Senate vote is compared with the 2022 governor vote.
