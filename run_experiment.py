"""python run_experiment.py <instance> [first last]
Stress test of processing-time uncertainty (the brief's required experiment) plus an optional replanning extension.
Executes six policies on every scenario file (paired: same files for every policy) and appends one line per
scenario to results/uncertainty/<instance>/results.jsonl.  Resumable.

Policies
  Fixed-Rule      deterministic plan of the earliest-start rule (method 1), executed without changes
  Fixed-Tabu      deterministic plan of makespan-only tabu search, executed without changes
  Fixed-TabuTW    deterministic plan of the default researched method (tabu + tardiness phase, slack 0), unchanged
  Replan-*        (optional extension) same starting plan, but when an operation completes more than
                  LATE_FRAC x H minutes later than the current plan expected, the unstarted operations are
                  re-planned by the same method (at most MAX_REPLANS times per scenario)
"""
import json, os, sys, time
from fjs.loader import load_instance
from fjs.checker import check, check_realized
from fjs.dispatch import earliest_start_rule
from fjs.tabu import tabu_search, tabu_search_tw
from fjs.cli import DEFAULTS
from fjs.scenarios import load_scenarios, horizon
from fjs.execution import run_policy

REPLAN_ITER, REPLAN_TW_ITER, REPLAN_STALL, REPLAN_SEED, DELTA = 400, 300, 100, 1, 10.0
LATE_FRAC, MAX_REPLANS = 0.05, 3          # trigger = 5% of the reference horizon H; at most 3 replans per scenario
name = sys.argv[1]
inst = load_instance(f"data/{name}")
LATE = LATE_FRAC * horizon(inst)
odir = f"results/uncertainty/{name}"
os.makedirs(odir, exist_ok=True)

plans_path = f"{odir}/plans.json"
if os.path.exists(plans_path):
    plans = json.load(open(plans_path))
else:
    t = time.time(); rule = earliest_start_rule(inst)
    tabu, i1 = tabu_search(inst, seed=1, iterations=DEFAULTS["iterations"], stall=DEFAULTS["stall"])
    tw, i2 = tabu_search_tw(inst, slack=0.0, seed=1, iterations=DEFAULTS["iterations"],
                            tw_iterations=DEFAULTS["tw_iterations"], stall=DEFAULTS["stall"])
    plans = {"Rule": rule, "Tabu": tabu, "TabuTW": tw}
    nominal = {}
    for k, s in plans.items():
        rep = check(inst, {"schedule": s}); assert rep["feasible"]
        nominal[k] = {"makespan": rep["measures"]["makespan"], "twt": rep["measures"]["total_weighted_tardiness"],
                      "on_time": rep["measures"]["on_time_fraction"]}
    plans = {"schedules": plans, "nominal": nominal}
    json.dump(plans, open(plans_path, "w"))
    print("plans built in %.1fs" % (time.time() - t), nominal, flush=True)

def timed(fn):
    box = {"t": 0.0}
    def w(sub):
        t = time.time(); r = fn(sub); box["t"] += time.time() - t; return r
    return w, box

PLANNERS = {
    "Rule": lambda sub: earliest_start_rule(sub),
    "Tabu": lambda sub: tabu_search(sub, seed=REPLAN_SEED, iterations=REPLAN_ITER, stall=REPLAN_STALL)[0],
    "TabuTW": lambda sub: tabu_search_tw(sub, slack=0.0, seed=REPLAN_SEED, iterations=REPLAN_ITER,
                                         tw_iterations=REPLAN_TW_ITER, stall=REPLAN_STALL)[0],
}
scens = load_scenarios(f"scenarios/{name}")
a = int(sys.argv[2]) if len(sys.argv) > 2 else 1
b = int(sys.argv[3]) if len(sys.argv) > 3 else len(scens)
rpath = f"{odir}/results.jsonl"
done = set()
if os.path.exists(rpath):
    done = {json.loads(l)["scenario_id"] for l in open(rpath)}
for sc in scens[a - 1:b]:
    if sc["scenario_id"] in done:
        continue
    rec = {"scenario_id": sc["scenario_id"]}
    for base in ("Rule", "Tabu", "TabuTW"):
        sched = plans["schedules"][base]
        for mode in ("Fixed", "Replan"):
            planner, box = (None, None) if mode == "Fixed" else timed(PLANNERS[base])
            r = run_policy(inst, sc, sched, planner=planner, delta=DELTA, late_threshold=LATE, max_replans=MAX_REPLANS)
            bad = check_realized(inst, sc, r["start"], r["completion"], r["assignment"])
            assert not bad, (sc["scenario_id"], mode, base, bad[:3])
            rec[f"{mode}-{base}"] = {k: r[k] for k in ("makespan", "twt", "on_time", "machine_changes", "start_shifts",
                                                       "replans", "replan_failures")}
            rec[f"{mode}-{base}"]["planner_s"] = round(box["t"], 3) if box else 0.0
    with open(rpath, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(sc["scenario_id"], {k: v["makespan"] for k, v in rec.items() if isinstance(v, dict) and "makespan" in v}, flush=True)
