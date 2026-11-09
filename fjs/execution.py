"""Executing a plan against realized data, and replanning during execution.

Plan = machine assignment {op: machine} + operation order {machine: [ops]}.

Fixed-plan execution rule (option brief): keep every assignment and every machine's order; start each
operation as soon as the machine is free and the job's previous operation has completed (or the job is
released), using the realized processing times.  (The engine can also pause work during machine down
intervals, which is tested but unused now that the brief has no breakdowns.)

Replanning policy (optional extension, not required by the brief):
  Trigger  - an operation completes more than `late_threshold` minutes after the completion time the current
             plan expected for it; at most `max_replans` times per scenario.
  At the trigger time t:
  * every operation that has started by t is frozen (machine and position never change);
  * the remaining operations are re-planned by a method on a sub-instance built ONLY from information known
    at t: nominal processing times for unstarted operations, actual completion times of finished operations,
    and for operations in progress an estimate (nominal time minus the minutes already worked).
    Realized durations of unstarted or unfinished operations are never read.
"""
import copy
from .loader import Instance


# ---------------------------------------------------------------- breakdown arithmetic
def merge(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def finish(t, rem, intervals):
    """Time at which `rem` minutes of work, begun no earlier than t, complete on a machine that is down during
    the (merged, sorted) `intervals`.  Work cannot start inside a down interval; running work pauses."""
    for us, ue in intervals:
        if ue <= t:
            continue
        if us <= t:
            t = ue
            continue
        if t + rem <= us:
            break
        rem -= us - t
        t = ue
    return t + rem


def worked(start, t, intervals):
    """Productive minutes between start and t on a machine that was down during `intervals`."""
    down = sum(max(0.0, min(t, ue) - max(start, us)) for us, ue in intervals)
    return max(0.0, (t - start) - down)


def down_by_machine(breakdowns):
    d = {}
    for b in breakdowns:
        d.setdefault(b["machine_id"], []).append((b["unavailable_start"], b["unavailable_end"]))
    return {m: merge(v) for m, v in d.items()}


# ---------------------------------------------------------------- plan helpers
def plan_from_schedule(entries):
    asg, seq = {}, {}
    for e in sorted(entries, key=lambda e: (e["start"], e["operation_id"])):
        asg[e["operation_id"]] = e["machine_id"]
        seq.setdefault(e["machine_id"], []).append(e["operation_id"])
    return asg, seq


def execute(inst, asg, seq, real_p, breakdowns):
    """Realized start and completion of every operation under the fixed-plan execution rule."""
    down = down_by_machine(breakdowns)
    mpred = {}
    for lst in seq.values():
        for a, b in zip(lst, lst[1:]):
            mpred[b] = a
    jpred = {}
    for ops in inst.job_ops.values():
        for a, b in zip(ops, ops[1:]):
            jpred[b] = a
    indeg = {o: (o in jpred) + (o in mpred) for o in inst.op_ids}
    succ = {o: [] for o in inst.op_ids}
    for o, a in list(jpred.items()) + list(mpred.items()):
        succ[a].append(o)
    stack = [o for o in inst.op_ids if indeg[o] == 0]
    start, comp = {}, {}
    done = 0
    while stack:
        o = stack.pop()
        done += 1
        m = asg[o]
        ready = inst.avail_start[m]
        if o in jpred:
            ready = max(ready, comp[jpred[o]])
        else:
            ready = max(ready, inst.release[inst.op_job[o]])
        if o in mpred:
            ready = max(ready, comp[mpred[o]])
        iv = down.get(m, [])
        s = finish(ready, 0.0, iv)
        start[o] = s
        comp[o] = finish(s, real_p[o][m], iv)
        for x in succ[o]:
            indeg[x] -= 1
            if indeg[x] == 0:
                stack.append(x)
    if done < len(inst.op_ids):
        raise ValueError("plan contains a cycle")
    return start, comp


def measures(inst, comp):
    jc = {j: comp[inst.job_ops[j][-1]] for j in inst.job_ids}
    return {"makespan": max(comp.values()),
            "twt": sum(inst.weight[j] * max(0.0, jc[j] - inst.due[j]) for j in inst.job_ids),
            "on_time": sum(1 for j in inst.job_ids if jc[j] <= inst.due[j] + 1e-8) / len(inst.job_ids)}


# ---------------------------------------------------------------- replanning
def subinstance(inst, remaining, job_ready, machine_free):
    jobs = [j for j in inst.job_ids if any(o in remaining for o in inst.job_ops[j])]
    job_ops = {j: [o for o in inst.job_ops[j] if o in remaining] for j in jobs}
    op_ids = [o for o in inst.op_ids if o in remaining]
    return Instance(
        name=inst.name + "/remaining", job_ids=jobs,
        release={j: job_ready[j] for j in jobs}, due={j: inst.due[j] for j in jobs},
        weight={j: inst.weight[j] for j in jobs}, op_ids=op_ids,
        op_job={o: inst.op_job[o] for o in op_ids},
        op_seq={o: inst.op_seq[o] for o in op_ids}, op_type={o: inst.op_type[o] for o in op_ids},
        job_ops=job_ops, alt={o: dict(inst.alt[o]) for o in op_ids},
        machine_ids=list(inst.machine_ids), machine_type=dict(inst.machine_type),
        avail_start={m: machine_free[m] for m in inst.machine_ids},
        avail_end=dict(inst.avail_end))


def run_policy(inst, scen, schedule, planner=None, delta=10.0, late_threshold=5.0, max_replans=3):
    """Execute `schedule` (a deterministic plan) under scenario `scen`.
    planner: None for the fixed plan, or a function sub_instance -> schedule entries (or None) used to
    re-plan when an operation completes more than `late_threshold` minutes later than the current plan
    expected.  Returns realized measures, stability counts and the times at which replanning happened."""
    real_p = scen["processing_time"]
    bds = scen.get("breakdowns", [])
    if planner is not None and bds:
        raise ValueError("replanning is defined for processing-time noise only (no breakdowns)")
    asg0, seq0 = plan_from_schedule(schedule)
    planned_start = {e["operation_id"]: e["start"] for e in schedule}
    expected = {e["operation_id"]: e["completion"] for e in schedule}   # completion the current plan expects
    asg, seq = dict(asg0), {m: list(v) for m, v in seq0.items()}
    t_last = -1.0
    replan_times, failed = [], 0
    while planner is not None and len(replan_times) < max_replans:
        start, comp = execute(inst, asg, seq, real_p, bds)
        late = [comp[o] for o in inst.op_ids if comp[o] > t_last and comp[o] - expected[o] > late_threshold]
        if not late:
            break
        t = min(late)                            # the late operation has just completed: this is revealed at t
        t_last = t
        started = {o for o in inst.op_ids if start[o] <= t}
        remaining = {o for o in inst.op_ids if o not in started}
        if not remaining:
            break

        def est_completion(o):
            """What the planner believes at time t about a started operation."""
            if comp[o] <= t:
                return comp[o]                   # finished: its actual completion is known
            return t + max(0.0, inst.alt[o][asg[o]] - (t - start[o]))

        job_ready = {}
        for j in inst.job_ids:
            done = [o for o in inst.job_ops[j] if o in started]
            if len(done) == len(inst.job_ops[j]):
                continue
            last = est_completion(done[-1]) if done else inst.release[j]
            job_ready[j] = max(float(t), float(last))
        machine_free = {}
        for m in inst.machine_ids:
            fr = [o for o in seq.get(m, []) if o in started]
            base = est_completion(fr[-1]) if fr else t
            machine_free[m] = max(float(t), float(base), inst.avail_start[m])
        sub = subinstance(inst, remaining, job_ready, machine_free)
        new = planner(sub)
        replan_times.append(t)
        for o in started:
            if comp[o] > t:
                expected[o] = est_completion(o)  # do not count the same delay twice
        if new is None:
            failed += 1                          # keep the old plan for the unstarted operations
            continue
        for o in remaining:
            seq[asg[o]].remove(o)
        by_m = {}
        for e in sorted(new, key=lambda e: (e["start"], e["operation_id"])):
            asg[e["operation_id"]] = e["machine_id"]
            expected[e["operation_id"]] = e["completion"]
            by_m.setdefault(e["machine_id"], []).append(e["operation_id"])
        for m, ops in by_m.items():
            seq.setdefault(m, []).extend(ops)
    start, comp = execute(inst, asg, seq, real_p, bds)
    out = measures(inst, comp)
    out.update({
        "machine_changes": sum(1 for o in inst.op_ids if asg[o] != asg0[o]),
        "start_shifts": sum(1 for o in inst.op_ids if abs(start[o] - planned_start[o]) > delta),
        "replans": len(replan_times), "replan_times": replan_times, "replan_failures": failed,
        "start": start, "completion": comp, "assignment": asg})
    return out
