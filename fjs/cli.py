"""Command-line entry point:  python solve.py <input_dir> <output_dir> [options]

Exit status (project guide, Table 6):
  malformed input ........ nonzero, message names file / row / column, no decision.json
  valid, no feasible ..... 0, "no feasible decision found", no decision.json
  valid, provably infeasible 0, "infeasible: <evidence>", no decision.json
"""
import argparse
import json
import os
import sys
import time

from .loader import load_instance, infeasibility_evidence, InputError
from .checker import check
from .dispatch import earliest_start_rule
from .tabu import tabu_search, tabu_search_tw

# Default settings of the better method (fixed seed, stops on an iteration count; the time cap is a safety net)
DEFAULTS = {"method": "tabu_tw", "seed": 1, "iterations": 5000, "tw_iterations": 2500, "slack": 0.0,
            "stall": 150, "time_cap": 240.0}


def run(input_dir, output_dir, method="tabu_tw", seed=1, iterations=5000, stall=150, time_cap=240.0,
        milp_time=60.0, slack=0.0, tw_iterations=2500, out=sys.stdout, err=sys.stderr):
    t0 = time.time()
    os.makedirs(output_dir, exist_ok=True)
    stale = os.path.join(output_dir, "decision.json")
    if os.path.exists(stale):
        os.remove(stale)                        # never leave a decision from an earlier run
    try:
        inst = load_instance(input_dir)
    except InputError as e:
        print(f"INVALID INPUT: {e}", file=err)
        _write_summary(output_dir, {"status": f"invalid input: {e}"})
        return 2
    for w in inst.warnings:
        print(f"WARNING: {w}", file=err)
    evidence = infeasibility_evidence(inst)
    if evidence:
        msg = f"infeasible: {evidence}"
        print(msg, file=out)
        _write_summary(output_dir, {"status": msg, "instance": inst.name})
        return 0

    info = {}
    if method == "rule":
        sched = earliest_start_rule(inst)
        info = {"method": "earliest-start dispatching (rule to beat)"}
    elif method == "tabu":
        sched, info = tabu_search(inst, seed=seed, iterations=iterations, stall=stall, time_cap=time_cap)
        info["method"] = "tabu search on the disjunctive graph"
    elif method == "tabu_tw":
        sched, info = tabu_search_tw(inst, slack=slack, seed=seed, iterations=iterations,
                                     tw_iterations=tw_iterations, stall=stall, time_cap=time_cap)
        info["method"] = "tabu search + tardiness phase (slack %.3f)" % slack
    elif method == "milp":
        from .milp import solve_milp
        sched, info = solve_milp(inst, time_limit=milp_time)
        info["method"] = "MILP (HiGHS)"
    else:
        print(f"unknown method {method!r}", file=err)
        return 2
    if sched is None:
        msg = "no feasible decision found"
        print(msg, file=out)
        _write_summary(output_dir, {"status": msg, "instance": inst.name, **info})
        return 0

    decision = {"schedule": sched}
    report = check(inst, decision)
    if report["measures"]:
        report["measures"] = _tidy(report["measures"])              # independent recomputation before anything is exported
    if not report["feasible"]:
        print("INTERNAL ERROR: decision failed the independent checker:\n  " +
              "\n  ".join(report["violations"][:10]), file=err)
        _write_summary(output_dir, {"status": "internal error: checker rejected decision",
                                    "violations": report["violations"][:20]})
        return 3
    with open(os.path.join(output_dir, "decision.json"), "w") as fh:
        json.dump(decision, fh, indent=1)
    summary = {"status": "feasible, checked", "instance": inst.name, "run_seconds": round(time.time() - t0, 3),
               **info, "measures": report["measures"]}
    _write_summary(output_dir, summary)
    print(_text(summary), file=out)
    return 0


def _tidy(x):
    """Show integral floats as integers (inputs are integer minutes)."""
    if isinstance(x, dict):
        return {k: _tidy(v) for k, v in x.items()}
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _text(s):
    m = s.get("measures")
    lines = [f"instance: {s.get('instance')}", f"status:   {s['status']}"]
    if "run_seconds" in s:
        lines.append(f"computation time (s, includes loading and checking): {s['run_seconds']}")
    for k in ("method", "start_solution", "seed", "time_cap_s", "iterations", "tw_iterations", "restarts",
              "stop_reason", "phase1_stop_reason", "phase2_stop_reason"):
        if k in s:
            lines.append(f"{k}: {s[k]}")
    if m:
        lines += [f"makespan: {m['makespan']}",
                  f"total weighted tardiness: {m['total_weighted_tardiness']}"]
    return "\n".join(lines)


def _write_summary(output_dir, s):
    with open(os.path.join(output_dir, "summary.json"), "w") as fh:
        json.dump(s, fh, indent=1)
    with open(os.path.join(output_dir, "summary.txt"), "w") as fh:
        fh.write(_text(s) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flexible job shop scheduler")
    ap.add_argument("input_dir")
    ap.add_argument("output_dir")
    ap.add_argument("--method", choices=["tabu_tw", "tabu", "rule", "milp"], default=DEFAULTS["method"])
    ap.add_argument("--seed", type=int, default=DEFAULTS["seed"])
    ap.add_argument("--iterations", type=int, default=DEFAULTS["iterations"])
    ap.add_argument("--stall", type=int, default=DEFAULTS["stall"])
    ap.add_argument("--time-cap", type=float, default=DEFAULTS["time_cap"])
    ap.add_argument("--milp-time", type=float, default=60.0)
    ap.add_argument("--slack", type=float, default=DEFAULTS["slack"],
                    help="tabu_tw: allowed makespan increase over the phase-1 makespan (0.05 = 5%%)")
    ap.add_argument("--tw-iterations", type=int, default=DEFAULTS["tw_iterations"])
    a = ap.parse_args(argv)
    return run(a.input_dir, a.output_dir, a.method, a.seed, a.iterations, a.stall, a.time_cap, a.milp_time,
               a.slack, a.tw_iterations)


if __name__ == "__main__":
    sys.exit(main())
