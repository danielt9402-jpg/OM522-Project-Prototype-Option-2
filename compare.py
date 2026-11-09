"""python compare.py <instance> [--milp-time S] [--no-milp]   -> results/<instance>.json
Every decision is re-checked by fjs.checker before any number is recorded."""
import argparse, json, os, statistics, sys, time
from fjs.loader import load_instance
from fjs.checker import check
from fjs.dispatch import earliest_start_rule
from fjs.tabu import tabu_search
from fjs.cli import DEFAULTS

ap = argparse.ArgumentParser()
ap.add_argument("instance")
ap.add_argument("--milp-time", type=float, default=100.0)
ap.add_argument("--no-milp", action="store_true")
ap.add_argument("--no-tabu", action="store_true")
ap.add_argument("--seeds", type=int, default=5)
a = ap.parse_args()
path = os.path.join("data", a.instance)
out_path = os.path.join("results", a.instance + ".json")
res = json.load(open(out_path)) if os.path.exists(out_path) else {}
t = time.time(); inst = load_instance(path); load_s = time.time() - t
res["instance"] = a.instance; res["operations"] = len(inst.op_ids); res["jobs"] = len(inst.job_ids)
res["load_seconds"] = round(load_s, 4)

def measured(sched):
    rep = check(inst, {"schedule": sched})
    assert rep["feasible"], rep["violations"][:3]
    m = rep["measures"]
    return {"makespan": m["makespan"], "twt": m["total_weighted_tardiness"], "on_time": m["on_time_fraction"],
            "utilization": m["machine_utilization"]}

t = time.time(); s = earliest_start_rule(inst); rt = time.time() - t
res["rule"] = {**measured(s), "runtime_s": round(rt, 4)}

if not a.no_tabu:
    runs = []
    for seed in range(1, a.seeds + 1):
        sched, info = tabu_search(inst, seed=seed, iterations=DEFAULTS["iterations"], stall=DEFAULTS["stall"],
                                  time_cap=DEFAULTS["time_cap"])
        runs.append({"seed": seed, **measured(sched), "runtime_s": info["runtime_s"],
                     "iterations": info["iterations"], "stop_reason": info["stop_reason"]})
    mk = [r["makespan"] for r in runs]
    res["tabu"] = {"settings": {k: DEFAULTS[k] for k in ("iterations", "stall", "time_cap")}, "runs": runs,
                   "makespan_mean": round(statistics.mean(mk), 2), "makespan_min": min(mk), "makespan_max": max(mk),
                   "twt_mean": round(statistics.mean(r["twt"] for r in runs), 1),
                   "on_time_mean": round(statistics.mean(r["on_time"] for r in runs), 3),
                   "runtime_mean_s": round(statistics.mean(r["runtime_s"] for r in runs), 2)}
if not a.no_milp:
    from fjs.milp import solve_milp
    sched, info = solve_milp(inst, time_limit=a.milp_time)
    res["milp"] = {**info, **(measured(sched) if sched else {})}
json.dump(res, open(out_path, "w"), indent=1)
print(a.instance, "rule", res["rule"]["makespan"], "| tabu", res.get("tabu", {}).get("makespan_min"),
      res.get("tabu", {}).get("makespan_mean"), res.get("tabu", {}).get("makespan_max"),
      "| milp", {k: res["milp"].get(k) for k in ("status", "makespan", "lower_bound", "runtime_s")} if "milp" in res else None)
