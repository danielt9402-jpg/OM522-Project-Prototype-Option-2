"""Construction methods. Each returns a list of schedule entries or None (no feasible decision)."""


def _entry(o, m, s, c):
    return {"operation_id": o, "machine_id": m, "start": s, "completion": c}


def earliest_start_rule(inst):
    """The rule to beat, exactly as specified in the brief (ties: completion, op id, machine id)."""
    last = dict(inst.avail_start)
    nxt = {j: 0 for j in inst.job_ids}
    ready = dict(inst.release)
    out = []
    remaining = len(inst.op_ids)
    while remaining:
        best = None
        for j in inst.job_ids:
            if nxt[j] >= len(inst.job_ops[j]):
                continue
            o = inst.job_ops[j][nxt[j]]
            for m, p in inst.alt[o].items():
                s = max(last[m], ready[j])
                if s + p > inst.avail_end[m]:
                    continue
                key = (s, s + p, o, m)
                if best is None or key < best:
                    best = key
        if best is None:
            return None
        s, c, o, m = best
        j = inst.op_job[o]
        out.append(_entry(o, m, s, c))
        last[m], ready[j] = c, c
        nxt[j] += 1
        remaining -= 1
    return out


def priority_dispatch(inst, priority="mwkr"):
    """Baseline: Giffler-Thompson style dispatching with machine choice by earliest completion.

    1. Every job's next operation, on each eligible machine, has a possible start and completion.
    2. Find the pair with the smallest completion time c* (ties: op id, machine id); call its machine m*.
    3. The conflict set is every candidate on m* whose possible start is below c*.
    4. Pick one conflict-set candidate by the priority rule (mwkr: most work remaining in the job,
       i.e. the sum of the job's shortest remaining processing times; ties: earlier start, op id).
    5. Schedule it, update the machine and job clocks, repeat until all operations are placed.
    The result is an active schedule: no operation can start earlier without delaying another.
    """
    last = dict(inst.avail_start)
    nxt = {j: 0 for j in inst.job_ids}
    ready = dict(inst.release)
    rem = {j: sum(min(inst.alt[o].values()) for o in inst.job_ops[j]) for j in inst.job_ids}
    out = []
    remaining = len(inst.op_ids)
    while remaining:
        cands = []
        for j in inst.job_ids:
            if nxt[j] >= len(inst.job_ops[j]):
                continue
            o = inst.job_ops[j][nxt[j]]
            for m, p in inst.alt[o].items():
                s = max(last[m], ready[j])
                if s + p <= inst.avail_end[m]:
                    cands.append((s, s + p, o, m, j))
        if not cands:
            return None
        cstar = min((c, o, m) for (s, c, o, m, j) in cands)
        mstar = cstar[2]
        conflict = [x for x in cands if x[3] == mstar and x[0] < cstar[0]]
        if priority == "mwkr":
            pick = min(conflict, key=lambda x: (-rem[x[4]], x[0], x[2]))
        elif priority == "edd":
            pick = min(conflict, key=lambda x: (inst.due[x[4]], x[0], x[2]))
        else:  # ect
            pick = min(conflict, key=lambda x: (x[1], x[2], x[3]))
        s, c, o, m, j = pick
        out.append(_entry(o, m, s, c))
        last[m], ready[j] = c, c
        rem[j] -= min(inst.alt[o].values())
        nxt[j] += 1
        remaining -= 1
    return out
