"""python compare_tw.py <instance> <first_seed> <last_seed>  -> adds tardiness-aware runs to results/<instance>.json
Resumable: a seed already present is skipped.  Every decision is re-checked by fjs.checker first."""
import json, os, sys
from fjs.loader import load_instance
from fjs.checker import check
from fjs.tabu import tabu_search_tw_multi
from fjs.cli import DEFAULTS

SLACKS = [0.0, 0.06]
name, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
inst = load_instance(os.path.join("data", name))
path = os.path.join("results", name + ".json")
res = json.load(open(path))
tw = res.setdefault("tabu_tw", {"settings": {"iterations": DEFAULTS["iterations"], "tw_iterations": DEFAULTS["tw_iterations"],
                                             "stall": DEFAULTS["stall"], "time_cap": DEFAULTS["time_cap"],
                                             "slacks": SLACKS}, "runs": []})
tw["settings"]["time_cap"] = DEFAULTS["time_cap"]
done = {r["seed"] for r in tw["runs"]}
for seed in range(a, b + 1):
    if seed in done:
        continue
    out = tabu_search_tw_multi(inst, SLACKS, seed=seed, iterations=DEFAULTS["iterations"],
                               tw_iterations=DEFAULTS["tw_iterations"], stall=DEFAULTS["stall"],
                               time_cap=DEFAULTS["time_cap"])
    for slack, (sched, info) in out.items():
        rep = check(inst, {"schedule": sched})
        assert rep["feasible"], rep["violations"][:3]
        m = rep["measures"]
        tw["runs"].append({"seed": seed, "slack": slack, "makespan": m["makespan"], "twt": m["total_weighted_tardiness"],
                           "on_time": m["on_time_fraction"], "runtime_s": info["runtime_s"], "stop_reason": info["stop_reason"]})
    json.dump(res, open(path, "w"), indent=1)
    print(name, "seed", seed, [(r["slack"], r["makespan"], r["twt"]) for r in tw["runs"] if r["seed"] == seed], flush=True)
