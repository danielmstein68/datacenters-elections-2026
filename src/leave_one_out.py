"""Leave-one-state-out check (added 2026-11-18 at Daniel's request, after the unofficial run): reruns the
main ring model and the utility-bill test dropping one state at a time."""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
import pandas as pd
import estimate_2026 as e
panel = pd.concat([p for st in e.STATES if (p := e.load_state(st)) is not None], ignore_index=True)
rows = []
for drop in [None] + sorted(panel.state.unique()):
    d = panel if drop is None else panel[panel.state != drop]
    lab = "all" if drop is None else f"drop {drop}"
    f = e.fit(d, "gop", "ring_any", lab); rows.append(f)
    rows.append(e.fit_utility(d, "gop", "dc_load_ratio_wave", lab))
    rows.append(e.fit_utility(d, "incparty", "dc_load_ratio_wave", lab))
c = pd.concat(rows)
c.to_csv(e.OUT / "unofficial_20261121/leave_one_out.csv", index=False)
r = c[c.outcome == "gop"].pivot_table(index="spec", columns="ring", values="coef")
s = c[c.outcome == "gop"].pivot_table(index="spec", columns="ring", values="se")
print(r.round(2).to_string()); print(s.round(2).to_string())
print(c[c.outcome == "incparty"][["spec", "coef", "se"]].round(2).to_string(index=False))
