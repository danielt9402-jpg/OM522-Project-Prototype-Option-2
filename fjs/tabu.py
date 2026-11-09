"""Tabu search on the disjunctive-graph representation of the flexible job shop.

Solution  = machine assignment for every operation + an operation order on every machine.
            A solution is feasible iff the graph of job-route arcs and machine-order arcs is acyclic;
            start times are then the earliest ("semi-active") times, found with a longest-path pass.
Objective = lexicographic (makespan, total weighted tardiness): makespan is the primary measure,
            tardiness breaks ties so the search can move across makespan plateaus.
Neighbors = built from critical operations only (those on a longest path):
            (a) swap two adjacent operations joined by a critical arc on one machine
                (never creates a cycle, Van Laarhoven et al. 1992);
            (b) move a critical operation to another eligible machine, at the sequence positions
                nearest to where it could start (infeasible/cyclic insertions are discarded).
Tabu list = (a) forbids swapping the pair back to its old order; (b) forbids moving the operation
            back to the machine it just left. A tabu move is allowed if it beats the best solution found.
Stopping  = a fixed number of iterations (deterministic given the seed); wall-clock cap is a safety net.
Restarts  = after `stall` iterations without a new best, jump back to the best solution and perturb it.

Tardiness-aware variant (`tabu_search_tw`): phase 1 is the search above.  Phase 2 restarts from its best
solution and minimises (total weighted tardiness, makespan) subject to makespan <= (1 + slack) x the phase-1
makespan.  Moves are generated from the "tight chains" that lead to each tardy job's last operation (every
operation whose completion equals the start of the next operation on the chain), because shortening the
makespan-critical path does nothing for a tardy job that is off it.  Swapping the two ends of a tight arc
cannot create a cycle (an alternative path between them would make the arc non-tight).
"""
import random
import time

from .dispatch import earliest_start_rule, priority_dispatch


class Model:
    def __init__(self, inst):
        self.inst = inst
        self.ops = list(inst.op_ids)
        self.n = len(self.ops)
        self.oi = {o: i for i, o in enumerate(self.ops)}
        self.machines = list(inst.machine_ids)
        self.M = len(self.machines)
        self.mi = {m: i for i, m in enumerate(self.machines)}
        jobs = inst.job_ids
        self.job_of = [jobs.index(inst.op_job[o]) for o in self.ops]
        self.jp = [-1] * self.n
        self.js = [-1] * self.n
        for j in jobs:
            lst = [self.oi[o] for o in inst.job_ops[j]]
            for a, b in zip(lst, lst[1:]):
                self.jp[b] = a
                self.js[a] = b
        self.rel = [inst.release[inst.op_job[o]] for o in self.ops]   # used by first ops only
        self.due = [inst.due[j] for j in jobs]
        self.wt = [inst.weight[j] for j in jobs]
        self.last_op = [self.oi[inst.job_ops[j][-1]] for j in jobs]
        self.astart = [inst.avail_start[m] for m in self.machines]
        self.aend = [inst.avail_end[m] for m in self.machines]
        self.P = [{self.mi[m]: p for m, p in inst.alt[o].items()} for o in self.ops]

    # ---- evaluation -----------------------------------------------------------------
    def evaluate(self, asg, seq):
        """Return (makespan, twt, start, comp, order) or None if the graph has a cycle / leaves availability."""
        n, jp, js, P = self.n, self.jp, self.js, self.P
        mp = [-1] * n
        msu = [-1] * n
        for lst in seq:
            prev = -1
            for o in lst:
                if prev >= 0:
                    mp[o] = prev
                    msu[prev] = o
                prev = o
        indeg = [(jp[o] >= 0) + (mp[o] >= 0) for o in range(n)]
        stack = [o for o in range(n) if indeg[o] == 0]
        start = [0] * n
        comp = [0] * n
        order = []
        astart = self.astart
        rel = self.rel
        while stack:
            o = stack.pop()
            order.append(o)
            m = asg[o]
            s = astart[m]
            a = jp[o]
            if a >= 0:
                if comp[a] > s:
                    s = comp[a]
            elif rel[o] > s:
                s = rel[o]
            b = mp[o]
            if b >= 0 and comp[b] > s:
                s = comp[b]
            start[o] = s
            comp[o] = s + P[o][m]
            for x in (js[o], msu[o]):
                if x >= 0:
                    indeg[x] -= 1
                    if indeg[x] == 0:
                        stack.append(x)
        if len(order) < n:
            return None
        aend = self.aend
        cmax = 0
        for o in range(n):
            if comp[o] > aend[asg[o]]:
                return None
            if comp[o] > cmax:
                cmax = comp[o]
        twt = 0
        for k, o in enumerate(self.last_op):
            if comp[o] > self.due[k]:
                twt += self.wt[k] * (comp[o] - self.due[k])
        return cmax, twt, start, comp, order

    def critical(self, asg, seq, start, comp, order, cmax):
        """Operations on a longest path, found by a backward tail computation."""
        n, jp, js, P = self.n, self.jp, self.js, self.P
        msu = [-1] * n
        for lst in seq:
            for a, b in zip(lst, lst[1:]):
                msu[a] = b
        q = [0] * n
        for o in reversed(order):
            best = 0
            for x in (js[o], msu[o]):
                if x >= 0:
                    t = P[x][asg[x]] + q[x]
                    if t > best:
                        best = t
            q[o] = best
        return [o for o in range(n) if start[o] + P[o][asg[o]] + q[o] == cmax]

    # ---- conversion -------------------------------------------------------------------
    def from_schedule(self, entries):
        asg = [0] * self.n
        per = [[] for _ in range(self.M)]
        for e in entries:
            o = self.oi[e["operation_id"]]
            m = self.mi[e["machine_id"]]
            asg[o] = m
            per[m].append((e["start"], e["completion"], o))
        seq = [[o for _, _, o in sorted(l)] for l in per]
        return asg, seq

    def to_schedule(self, asg, start, comp):
        rows = [{"operation_id": self.ops[o], "machine_id": self.machines[asg[o]],
                 "start": start[o], "completion": comp[o]} for o in range(self.n)]
        rows.sort(key=lambda r: (r["start"], r["operation_id"]))
        return rows


def _key(res):
    return (res[0], res[1])


def _construct(inst, model):
    """Best of several dispatch schedules, as (key, name, asg, seq) or None."""
    starts = []
    for name, fn in (("earliest_start", earliest_start_rule),
                     ("gt_mwkr", lambda i: priority_dispatch(i, "mwkr")),
                     ("gt_ect", lambda i: priority_dispatch(i, "ect")),
                     ("gt_edd", lambda i: priority_dispatch(i, "edd"))):
        sch = fn(inst)
        if sch is None:
            continue
        asg, seq = model.from_schedule(sch)
        res = model.evaluate(asg, seq)
        if res is not None:
            starts.append((_key(res), name, asg, seq))
    if not starts:
        return None
    starts.sort(key=lambda t: t[0])
    return starts[0]


def _critical_ops(model):
    return lambda asg, seq, res: model.critical(asg, seq, res[2], res[3], res[4], res[0])


def _tardy_ops(model):
    """Operations on tight chains leading to the last operation of every tardy job."""
    def fn(asg, seq, res):
        start, comp = res[2], res[3]
        mp = [-1] * model.n
        for lst in seq:
            for a, b in zip(lst, lst[1:]):
                mp[b] = a
        chain = set()
        for k, o_last in enumerate(model.last_op):
            if comp[o_last] > model.due[k]:
                stack = [o_last]
                while stack:
                    o = stack.pop()
                    if o in chain:
                        continue
                    chain.add(o)
                    for a in (model.jp[o], mp[o]):
                        if a >= 0 and comp[a] == start[o]:
                            stack.append(a)
        return sorted(chain)
    return fn


def _search(model, asg, seq, rng, iterations, stall, tenure, t0, time_cap, key_fn, ok_fn, ops_fn,
            stop_at=None, log=None):
    """Generic tabu loop.  Minimises key_fn(res) over solutions with ok_fn(res) true.
    Returns (best_key, best_asg, best_seq, stats)."""
    cur = model.evaluate(asg, seq)
    best_key, best_asg, best_seq = key_fn(cur), list(asg), [list(l) for l in seq]
    tabu = {}
    since_best = 0
    it = 0
    restarts = 0
    stop = "iteration limit"

    def neighbors(asg, seq, res):
        _, _, start, comp, _ = res
        crit = ops_fn(asg, seq, res)
        pos = {}
        for m, lst in enumerate(seq):
            for k, o in enumerate(lst):
                pos[o] = k
        out = []
        cset = set(crit)
        for o in crit:
            m = asg[o]
            k = pos[o]
            lst = seq[m]
            # (a) swap with machine successor over a tight arc
            if k + 1 < len(lst):
                v = lst[k + 1]
                if v in cset and start[v] == comp[o]:
                    out.append(("swap", m, k, o, v))
            # (b) reassign to another eligible machine
            if len(model.P[o]) > 1:
                a = model.jp[o]
                est = comp[a] if a >= 0 else model.rel[o]
                for m2 in model.P[o]:
                    if m2 == m:
                        continue
                    tgt = seq[m2]
                    k0 = 0
                    while k0 < len(tgt) and start[tgt[k0]] < est:
                        k0 += 1
                    for p in {max(0, k0 - 1), k0, min(len(tgt), k0 + 1)}:
                        out.append(("move", m, k, o, m2, p))
        return out

    def apply(asg, seq, mv):
        if mv[0] == "swap":
            _, m, k, u, v = mv
            seq[m][k], seq[m][k + 1] = v, u
        else:
            _, m, k, o, m2, p = mv
            del seq[m][k]
            seq[m2].insert(p, o)
            asg[o] = m2

    def undo(asg, seq, mv):
        if mv[0] == "swap":
            _, m, k, u, v = mv
            seq[m][k], seq[m][k + 1] = u, v
        else:
            _, m, k, o, m2, p = mv
            del seq[m2][p]
            seq[m].insert(k, o)
            asg[o] = m

    def tabu_forbids(mv, it):
        if mv[0] == "swap":
            return tabu.get(("order", mv[4], mv[3]), -1) >= it
        return tabu.get(("mach", mv[3], mv[4]), -1) >= it

    while it < iterations:
        if time.time() - t0 > time_cap:
            stop = "time cap"
            break
        if stop_at is not None and best_key[0] <= stop_at:
            stop = "target reached"
            break
        it += 1
        moves = neighbors(asg, seq, cur)
        if not moves:
            stop = "no moves"
            break
        cand = []
        for mv in moves:
            apply(asg, seq, mv)
            r = model.evaluate(asg, seq)
            undo(asg, seq, mv)
            if r is None or not ok_fn(r):
                continue
            k = key_fn(r)
            if tabu_forbids(mv, it) and not k < best_key:
                continue
            cand.append((k, rng.random(), mv))
        if not cand:
            tabu.clear()
            since_best += 1
        else:
            cand.sort(key=lambda t: (t[0], t[1]))
            k, _, mv = cand[0]
            if mv[0] == "swap":
                tabu[("order", mv[3], mv[4])] = it + rng.randint(*tenure)
            else:
                tabu[("mach", mv[3], mv[1])] = it + rng.randint(*tenure)
            apply(asg, seq, mv)
            cur = model.evaluate(asg, seq)
            if k < best_key:
                best_key, best_asg, best_seq = k, list(asg), [list(l) for l in seq]
                since_best = 0
                if log:
                    log(f"  it {it}: best {k}")
            else:
                since_best += 1
        if since_best >= stall:
            restarts += 1
            asg, seq = list(best_asg), [list(l) for l in best_seq]
            cur = model.evaluate(asg, seq)
            for _ in range(rng.randint(3, 6)):
                mvs = neighbors(asg, seq, cur)
                rng.shuffle(mvs)
                for mv in mvs:
                    apply(asg, seq, mv)
                    r = model.evaluate(asg, seq)
                    if r is not None and ok_fn(r):
                        cur = r
                        break
                    undo(asg, seq, mv)
            tabu.clear()
            since_best = 0
    return best_key, best_asg, best_seq, {"iterations": it, "restarts": restarts, "stop_reason": stop}


def tabu_search(inst, seed=1, iterations=600, stall=120, tenure=(8, 14), time_cap=240.0, log=None):
    """Makespan-first tabu search.  Return (schedule_entries or None, info dict)."""
    t0 = time.time()
    model = Model(inst)
    rng = random.Random(seed)
    first = _construct(inst, model)
    if first is None:
        return None, {"status": "no feasible decision found", "iterations": 0}
    init_key, init_name, asg, seq = first
    best_key, best_asg, best_seq, st = _search(
        model, list(asg), [list(l) for l in seq], rng, iterations, stall, tenure, t0, time_cap,
        key_fn=_key, ok_fn=lambda r: True, ops_fn=_critical_ops(model), log=log)
    final = model.evaluate(best_asg, best_seq)
    sched = model.to_schedule(best_asg, final[2], final[3])
    info = {"status": "feasible", "start_solution": init_name, "start_makespan": init_key[0], **st,
            "seed": seed, "time_cap_s": time_cap, "runtime_s": round(time.time() - t0, 3)}
    return sched, info


def tabu_search_tw(inst, slack=0.0, seed=1, iterations=5000, tw_iterations=3000, stall=150,
                   tenure=(8, 14), time_cap=240.0, log=None):
    """Phase 1: makespan-first tabu search.  Phase 2: minimise weighted tardiness with
    makespan <= (1 + slack) x phase-1 makespan.  Return (schedule_entries or None, info dict)."""
    out = tabu_search_tw_multi(inst, [slack], seed, iterations, tw_iterations, stall, tenure, time_cap, log)
    return out[slack] if out is not None else (None, {"status": "no feasible decision found", "iterations": 0})


def tabu_search_tw_multi(inst, slacks, seed=1, iterations=5000, tw_iterations=3000, stall=150,
                         tenure=(8, 14), time_cap=240.0, log=None):
    """Run phase 1 once and phase 2 for each slack (each phase 2 starts from the same phase-1 solution and
    the same random-number state, so every entry equals a stand-alone tabu_search_tw call).
    Returns {slack: (schedule, info)} or None if no start solution exists."""
    import copy
    t0 = time.time()
    model = Model(inst)
    rng = random.Random(seed)
    first = _construct(inst, model)
    if first is None:
        return None
    init_key, init_name, asg, seq = first
    k1, a1, s1, st1 = _search(
        model, list(asg), [list(l) for l in seq], rng, iterations, stall, tenure, t0, time_cap,
        key_fn=_key, ok_fn=lambda r: True, ops_fn=_critical_ops(model), log=log)
    t1 = time.time() - t0
    out = {}
    for slack in slacks:
        t2 = time.time()
        phase2_budget = max(0.0, time_cap - (t2 - t0))
        rng2 = copy.deepcopy(rng)
        cap = k1[0] * (1.0 + slack)
        k2, a2, s2, st2 = _search(
            model, list(a1), [list(l) for l in s1], rng2, tw_iterations, stall, tenure, t2, phase2_budget,
            key_fn=lambda r: (r[1], r[0]), ok_fn=lambda r: r[0] <= cap, ops_fn=_tardy_ops(model),
            stop_at=0, log=log)
        final = model.evaluate(a2, s2)
        sched = model.to_schedule(a2, final[2], final[3])
        out[slack] = (sched, {
            "status": "feasible", "start_solution": init_name, "slack": slack, "makespan_cap": cap,
            "phase1_makespan": k1[0], "phase1_twt": k1[1], "phase2_twt": k2[0], "phase2_makespan": k2[1],
            "iterations": st1["iterations"], "tw_iterations": st2["iterations"],
            "restarts": st1["restarts"] + st2["restarts"], "stop_reason": st2["stop_reason"],
            "phase1_stop_reason": st1["stop_reason"], "phase2_stop_reason": st2["stop_reason"],
            "seed": seed, "time_cap_s": time_cap, "runtime_s": round(t1 + time.time() - t2, 3)})
    return out
