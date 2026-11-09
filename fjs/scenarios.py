"""Scenario generation (independent of every method) and file I/O.

The brief's stress test varies one input: operation processing times.
  Each operation o gets one independent factor f_o ~ Lognormal with mean 1 and coefficient of variation CV
  (default 0.2).  The realized time of o on machine m is p[o,m] * f_o, so an operation that turns out long is
  long on every machine it could be moved to.  The factor becomes known when the operation completes.
Everything is drawn from random.Random(seed) with seed = BASE_SEED * 100000 + scenario_index, operations
visited in sorted id order, so a scenario never depends on which method will be run on it.  A scenario file
holds every realized processing time (a complete set of realized input values).  Machine availability, releases
and due dates are not varied.  The `breakdowns` field is always empty; the execution engine can pause work
during a down interval (tested), but the brief no longer asks for breakdowns, so none are generated.
"""
import json
import math
import os
import random

BASE_SEED = 2026
CV = 0.2


def horizon(inst):
    """Reference horizon from input data only: the larger of the longest job path (release + shortest
    processing times) and the heaviest machine-type load (shortest times / number of machines of that type).
    Used to scale the replanning trigger, never to build scenarios."""
    path = max(inst.release[j] + sum(min(inst.alt[o].values()) for o in inst.job_ops[j]) for j in inst.job_ids)
    by_type = {}
    for m in inst.machine_ids:
        by_type[inst.machine_type[m]] = by_type.get(inst.machine_type[m], 0) + 1
    load = 0.0
    for t, k in by_type.items():
        work = sum(min(inst.alt[o].values()) for o in inst.op_ids if inst.op_type[o] == t)
        load = max(load, work / k)
    return max(float(path), load)


def make_scenario(inst, index, cv=CV):
    rng = random.Random(BASE_SEED * 100000 + index)
    sigma2 = math.log(1.0 + cv * cv)
    mu = -sigma2 / 2.0                       # E[exp(N(mu, sigma2))] = 1
    sigma = math.sqrt(sigma2)
    factor = {o: rng.lognormvariate(mu, sigma) for o in sorted(inst.op_ids)}
    return {
        "scenario_id": f"S{index:03d}", "seed": BASE_SEED * 100000 + index, "instance": inst.name,
        "distribution": {"processing_time_factor": f"lognormal, mean 1, CV {cv}, one factor per operation, independent"},
        "duration_factor": factor,
        "processing_time": {o: {m: p * factor[o] for m, p in inst.alt[o].items()} for o in inst.op_ids},
        "breakdowns": [],
    }


def write_scenarios(inst, out_dir, count=30, **kw):
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.endswith(".json"):
            os.remove(os.path.join(out_dir, f))
    paths = []
    for i in range(1, count + 1):
        sc = make_scenario(inst, i, **kw)
        p = os.path.join(out_dir, sc["scenario_id"] + ".json")
        with open(p, "w") as fh:
            json.dump(sc, fh, indent=1, sort_keys=True)
        paths.append(p)
    return paths


def load_scenarios(dir_):
    out = []
    for f in sorted(os.listdir(dir_)):
        if f.endswith(".json"):
            with open(os.path.join(dir_, f)) as fh:
                out.append(json.load(fh))
    return out
