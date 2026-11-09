"""Independent checker: reads the input tables and decision.json only, recomputes everything.

Does not import any method. Tolerance 1e-8 on every comparison (as in the project guide),
except tardiness, which is computed from the completion time without tolerance.
"""
import json
import math

TOL = 1e-8


def check(inst, decision):
    """Return {'feasible': bool, 'violations': [str], 'measures': {...} or None}."""
    v = []
    sched = decision.get("schedule") if isinstance(decision, dict) else None
    if not isinstance(sched, list):
        return {"feasible": False, "violations": ["decision has no 'schedule' list"], "measures": None}

    entries, seen = {}, {}
    for k, e in enumerate(sched):
        try:
            o, m = str(e["operation_id"]), str(e["machine_id"])
            s, c = float(e["start"]), float(e["completion"])
        except (KeyError, TypeError, ValueError):
            v.append(f"schedule entry {k}: missing or non-numeric field")
            continue
        if o not in inst.op_job:
            v.append(f"schedule entry {k}: unknown operation {o}")
            continue
        seen[o] = seen.get(o, 0) + 1
        entries[o] = (m, s, c)
        if not math.isfinite(s) or not math.isfinite(c):
            v.append(f"schedule entry {k}: start and completion must be finite numbers")
    for o in inst.op_ids:
        if seen.get(o, 0) != 1:
            v.append(f"operation {o} appears {seen.get(o, 0)} times (must be exactly once)")

    # constraint 1: eligible machine and uninterrupted processing time
    for o, (m, s, c) in entries.items():
        if m not in inst.alt[o]:
            v.append(f"{o}: machine {m} is not eligible for this operation")
            continue
        if abs((c - s) - inst.alt[o][m]) > TOL:
            v.append(f"{o}: duration {c - s} differs from processing time {inst.alt[o][m]} on {m}")

    # constraint 2: release and route order
    for j in inst.job_ids:
        prev_c, prev_o = None, None
        for o in inst.job_ops[j]:
            if o not in entries:
                break
            _, s, c = entries[o]
            if prev_c is None:
                if s < inst.release[j] - TOL:
                    v.append(f"{o}: starts at {s} before job {j} release {inst.release[j]}")
            elif s < prev_c - TOL:
                v.append(f"{o}: starts at {s} before {prev_o} completes at {prev_c}")
            prev_c, prev_o = c, o

    # constraint 3: no overlap on a machine, inside the availability interval
    by_m = {}
    for o, (m, s, c) in entries.items():
        by_m.setdefault(m, []).append((s, c, o))
    for m, lst in by_m.items():
        if m not in inst.machine_type:
            v.append(f"machine {m} does not exist")
            continue
        lst.sort()
        for s, c, o in lst:
            if s < inst.avail_start[m] - TOL or c > inst.avail_end[m] + TOL:
                v.append(f"{o}: [{s}, {c}) outside availability "
                         f"[{inst.avail_start[m]}, {inst.avail_end[m]}] of {m}")
        for (s1, c1, o1), (s2, c2, o2) in zip(lst, lst[1:]):
            if s2 < c1 - TOL:
                v.append(f"{o1} and {o2} overlap on {m}")

    if v:
        return {"feasible": False, "violations": v, "measures": None}

    # measures
    comp = {j: entries[inst.job_ops[j][-1]][2] for j in inst.job_ids}
    makespan = max(c for (_, _, c) in entries.values())
    twt = sum(inst.weight[j] * max(0.0, comp[j] - inst.due[j]) for j in inst.job_ids)
    ontime = sum(1 for j in inst.job_ids if comp[j] <= inst.due[j] + TOL) / len(inst.job_ids)
    busy = {m: 0.0 for m in inst.machine_ids}
    for o, (m, s, c) in entries.items():
        busy[m] += c - s
    util = {m: (busy[m] / makespan if makespan > 0 else 0.0) for m in inst.machine_ids}
    return {"feasible": True, "violations": [],
            "measures": {"makespan": makespan, "total_weighted_tardiness": twt,
                         "on_time_fraction": ontime, "jobs_completed": len(inst.job_ids),
                         "job_completion": comp, "machine_utilization": util}}


def check_file(inst, decision_path):
    with open(decision_path) as fh:
        return check(inst, json.load(fh))


def check_realized(inst, scen, start, comp, asg, tol=1e-6):
    """Independent check of a *realized* execution (not a decision file): every operation once on an eligible
    machine, the realized work equals the realized processing time (minutes inside breakdowns do not count), no
    operation starts inside a breakdown, route order and release respected, no overlap on a machine.
    Returns a list of violations (empty when valid).  Uses no code from fjs.execution."""
    v = []
    down = {}
    for b in scen["breakdowns"]:
        down.setdefault(b["machine_id"], []).append((b["unavailable_start"], b["unavailable_end"]))
    for o in inst.op_ids:
        m = asg.get(o)
        if m not in inst.alt[o]:
            v.append(f"{o}: machine {m} not eligible")
            continue
        s, c = start[o], comp[o]
        if not math.isfinite(s) or not math.isfinite(c):
            v.append(f"{o}: realized start and completion must be finite numbers")
            continue
        if s < inst.avail_start[m] - tol or c > inst.avail_end[m] + tol:
            v.append(f"{o}: [{s}, {c}) outside availability "
                     f"[{inst.avail_start[m]}, {inst.avail_end[m]}] of {m}")
        lost = sum(max(0.0, min(c, ue) - max(s, us)) for us, ue in down.get(m, []))
        if abs((c - s) - lost - scen["processing_time"][o][m]) > tol:
            v.append(f"{o}: worked {(c - s) - lost} != realized time {scen['processing_time'][o][m]}")
        if any(us <= s < ue for us, ue in down.get(m, [])):
            v.append(f"{o}: starts at {s} while {m} is down")
    for j in inst.job_ids:
        ops = inst.job_ops[j]
        if start[ops[0]] < inst.release[j] - tol:
            v.append(f"{ops[0]}: starts before release")
        for a, b in zip(ops, ops[1:]):
            if start[b] < comp[a] - tol:
                v.append(f"{b}: starts before {a} completes")
    by_m = {}
    for o in inst.op_ids:
        by_m.setdefault(asg[o], []).append((start[o], comp[o], o))
    for m, lst in by_m.items():
        lst.sort()
        for (s1, c1, o1), (s2, c2, o2) in zip(lst, lst[1:]):
            if s2 < c1 - tol:
                v.append(f"{o1} and {o2} overlap on {m}")
    return v
