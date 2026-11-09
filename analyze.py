"""python analyze.py <instance>  -> results/uncertainty/<instance>/summary.json, summary.md, policies.png
Observation = one scenario (n = number of scenario files).  Percentiles: numpy 'linear' across the n scenarios.
Paired differences use identical scenarios; the 95% interval is a percentile bootstrap over scenarios
(10,000 resamples, seed 7)."""
import json, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

name = sys.argv[1]
d = f"results/uncertainty/{name}"
rows = [json.loads(l) for l in open(f"{d}/results.jsonl")]
rows.sort(key=lambda r: r["scenario_id"])
plans = json.load(open(f"{d}/plans.json"))["nominal"]
inst_info = json.load(open(f"results/{name}.json"))
n_jobs, n_ops = inst_info["jobs"], inst_info["operations"]
n = len(rows)
POL = ["Fixed-Rule", "Replan-Rule", "Fixed-Tabu", "Replan-Tabu", "Fixed-TabuTW", "Replan-TabuTW"]
col = lambda p, k: np.array([r[p][k] for r in rows], dtype=float)

summ = {}
for p in POL:
    mk, tw, ot = col(p, "makespan"), col(p, "twt"), col(p, "on_time")
    base = p.split("-")[1]
    summ[p] = {"makespan_mean": mk.mean(), "makespan_sd": mk.std(ddof=1), "makespan_p90": np.percentile(mk, 90),
               "makespan_max": mk.max(), "nominal_makespan": plans[base]["makespan"],
               "twt_mean": tw.mean(), "twt_p90": np.percentile(tw, 90),
               "on_time_mean": ot.mean(), "machine_changes_mean": col(p, "machine_changes").mean(),
               "start_shifts_mean": col(p, "start_shifts").mean(), "replans_mean": col(p, "replans").mean(),
               "replan_failures_total": int(col(p, "replan_failures").sum()),
               "planner_seconds_mean": col(p, "planner_s").mean()}

rng = np.random.default_rng(7)
def paired(a, b, key="makespan"):
    """mean of (a - b) over scenarios; negative = a better (smaller) for makespan/twt."""
    x = col(a, key) - col(b, key)
    idx = rng.integers(0, n, size=(10000, n))
    bm = x[idx].mean(axis=1)
    return {"mean_diff": x.mean(), "ci95": [float(np.percentile(bm, 2.5)), float(np.percentile(bm, 97.5))],
            "a_better": int((x < -1e-6).sum()), "tie": int((abs(x) <= 1e-6).sum()), "b_better": int((x > 1e-6).sum())}
PAIRS = [("Replan-Rule", "Fixed-Rule"), ("Replan-Tabu", "Fixed-Tabu"), ("Replan-TabuTW", "Fixed-TabuTW"),
         ("Fixed-Tabu", "Fixed-Rule"), ("Replan-Tabu", "Replan-Rule"), ("Fixed-TabuTW", "Fixed-Tabu"),
         ("Replan-TabuTW", "Replan-Tabu"), ("Fixed-TabuTW", "Fixed-Rule"), ("Replan-TabuTW", "Replan-Rule")]
cmp = {f"{a} vs {b}": {"makespan": paired(a, b), "twt": paired(a, b, "twt")} for a, b in PAIRS}
json.dump({"instance": name, "scenarios": n, "jobs": n_jobs, "operations": n_ops, "policies": summ, "paired": cmp},
          open(f"{d}/summary.json", "w"), indent=1, default=float)

L = [f"## {name}: {n} scenarios, {n_jobs} jobs, {n_ops} operations\n",
     "Makespan in minutes. Mean, sd and 90th percentile are across the %d scenarios. On-time = jobs finished by due date / %d jobs. "
     "Machine changes and start shifts (|realized start - planned start| > 10) are counted per scenario out of %d operations.\n" % (n, n_jobs, n_ops),
     "| policy | nominal plan | mean | sd | p90 | max | mean TWT | p90 TWT | on-time | machine changes | start shifts |",
     "|---|---|---|---|---|---|---|---|---|---|---|"]
for p in POL:
    s = summ[p]
    L.append(f"| {p} | {s['nominal_makespan']:.0f} | {s['makespan_mean']:.1f} | {s['makespan_sd']:.1f} | {s['makespan_p90']:.1f} | "
             f"{s['makespan_max']:.1f} | {s['twt_mean']:.0f} | {s['twt_p90']:.0f} | {s['on_time_mean']:.3f} | "
             f"{s['machine_changes_mean']:.1f} | {s['start_shifts_mean']:.1f} |")
L += ["\nPaired differences (first minus second; negative = first is better). Win counts are scenarios out of %d.\n" % n,
      "| comparison | makespan mean diff [95% CI] | first better / tie / second better | TWT mean diff [95% CI] |", "|---|---|---|---|"]
for k, v in cmp.items():
    m, t = v["makespan"], v["twt"]
    L.append(f"| {k} | {m['mean_diff']:+.1f} [{m['ci95'][0]:+.1f}, {m['ci95'][1]:+.1f}] | {m['a_better']} / {m['tie']} / {m['b_better']} | "
             f"{t['mean_diff']:+.0f} [{t['ci95'][0]:+.0f}, {t['ci95'][1]:+.0f}] |")
open(f"{d}/summary.md", "w").write("\n".join(L) + "\n")
print("\n".join(L))

# ---- plot
colors = {"Rule": "#c0392b", "Tabu": "#2471a3", "TabuTW": "#1e8449"}
fig, ax = plt.subplots(1, 3, figsize=(17, 5.2), gridspec_kw={"width_ratios": [2.2, 1.2, 1.2]})
order = np.argsort(col("Fixed-Rule", "makespan"))
x = np.arange(n)
for p in POL:
    mode, base = p.split("-")
    ax[0].plot(x, col(p, "makespan")[order], marker="o" if mode == "Replan" else "s", ms=4.5, lw=0.8, alpha=0.85,
               ls="-" if mode == "Replan" else ":", color=colors[base], mfc=colors[base] if mode == "Replan" else "white", label=p)
ax[0].set_xlabel(f"scenario (sorted by Fixed-Rule makespan; each point is one of {n} scenarios)")
ax[0].set_ylabel("realized makespan (min)"); ax[0].set_title("Per-scenario makespan, identical scenarios for every policy")
ax[0].legend(fontsize=8, ncol=2); ax[0].grid(alpha=.25)
for a_, key, ttl in ((ax[1], "makespan", "Makespan"), (ax[2], "twt", "Total weighted tardiness")):
    data = [col(p, key) for p in POL]
    bp = a_.boxplot(data, vert=True, patch_artist=True, showfliers=True, widths=.6)
    for patch, p in zip(bp["boxes"], POL):
        patch.set_facecolor(colors[p.split("-")[1]]); patch.set_alpha(.35 if p.startswith("Fixed") else .8)
    for i, dat in enumerate(data, start=1):
        a_.plot(i, np.percentile(dat, 90), marker="^", color="k", ms=6, zorder=5)
    a_.set_xticks(range(1, 7)); a_.set_xticklabels([p.replace("-", "\n") for p in POL], fontsize=8)
    a_.set_title(f"{ttl}: box = quartiles, ▲ = 90th pct\nacross {n} scenarios", fontsize=9); a_.grid(alpha=.25, axis="y")
fig.suptitle(f"{name}: fixed plans and optional replanning under processing-time noise (lognormal, CV 0.2)", fontsize=11)
fig.tight_layout(); fig.savefig(f"{d}/policies.png", dpi=130)
