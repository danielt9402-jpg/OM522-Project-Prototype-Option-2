"""Exact benchmark: big-M mixed-integer model solved with HiGHS (scipy.optimize.milp).

Used only on smaller instances to prove optimality or to report a lower bound.
Minimise makespan.  Variables: start s_i, assignment z_im (binary), order y_ij (binary, per pair of
operations from different jobs that share an eligible machine), makespan Cmax.
"""
import time
import numpy as np
from scipy.optimize import milp, LinearConstraint, Bounds
from scipy.sparse import lil_matrix

from .dispatch import earliest_start_rule
from .tabu import Model


def solve_milp(inst, time_limit=60.0):
    t0 = time.time()
    mod = Model(inst)
    n = mod.n
    # horizon from the rule to beat (a valid upper bound); fall back to a loose bound
    rule = earliest_start_rule(inst)
    if rule is None:
        return None, {"status": "no feasible decision found"}
    H = float(max(e["completion"] for e in rule))
    pairs = []          # (i, j, shared machines)
    for i in range(n):
        for j in range(i + 1, n):
            if mod.job_of[i] == mod.job_of[j]:
                continue
            sh = [m for m in mod.P[i] if m in mod.P[j]]
            if sh:
                pairs.append((i, j, sh))
    # variable indexing
    idx = {}
    nv = 0
    for i in range(n):
        idx[("s", i)] = nv; nv += 1
    for i in range(n):
        for m in mod.P[i]:
            idx[("z", i, m)] = nv; nv += 1
    for (i, j, _) in pairs:
        idx[("y", i, j)] = nv; nv += 1
    idx["C"] = nv; nv += 1

    rows, lo, hi = [], [], []

    def add(coefs, l, u):
        rows.append(coefs); lo.append(l); hi.append(u)

    def c_expr(i):   # completion of i as {var: coef}
        d = {idx[("s", i)]: 1.0}
        for m, p in mod.P[i].items():
            d[idx[("z", i, m)]] = float(p)
        return d

    for i in range(n):
        add({idx[("z", i, m)]: 1.0 for m in mod.P[i]}, 1, 1)               # exactly one machine
        a = mod.jp[i]
        if a >= 0:                                                          # route precedence
            d = {idx[("s", i)]: 1.0}
            for k, v in c_expr(a).items():
                d[k] = d.get(k, 0.0) - v
            add(d, 0, np.inf)
        if mod.js[i] < 0:                                                   # makespan
            d = {idx["C"]: 1.0}
            for k, v in c_expr(i).items():
                d[k] = d.get(k, 0.0) - v
            add(d, 0, np.inf)
    for (i, j, sh) in pairs:
        y = idx[("y", i, j)]
        for m in sh:
            zi, zj = idx[("z", i, m)], idx[("z", j, m)]
            # i before j:  s_j >= c_i - H(1-y) - H(2 - zi - zj)
            d = {idx[("s", j)]: 1.0, y: -H, zi: -H, zj: -H}
            for k, v in c_expr(i).items():
                d[k] = d.get(k, 0.0) - v
            add(d, -3 * H, np.inf)
            # j before i:  s_i >= c_j - H*y - H(2 - zi - zj)
            d = {idx[("s", i)]: 1.0, y: H, zi: -H, zj: -H}
            for k, v in c_expr(j).items():
                d[k] = d.get(k, 0.0) - v
            add(d, -2 * H, np.inf)
    A = lil_matrix((len(rows), nv))
    for r, d in enumerate(rows):
        for k, v in d.items():
            A[r, k] = v
    lb = np.zeros(nv); ub = np.full(nv, H)
    integrality = np.zeros(nv)
    for i in range(n):
        first = mod.jp[i] < 0
        lb[idx[("s", i)]] = max(mod.rel[i] if first else 0, min(mod.astart[m] for m in mod.P[i]))
        ub[idx[("s", i)]] = H
    for key, k in idx.items():
        if key != "C" and key[0] in ("z", "y"):
            integrality[k] = 1; ub[k] = 1
    c = np.zeros(nv); c[idx["C"]] = 1.0
    res = milp(c, constraints=LinearConstraint(A.tocsr(), lo, hi), integrality=integrality,
               bounds=Bounds(lb, ub), options={"time_limit": time_limit, "disp": False})
    info = {"runtime_s": round(time.time() - t0, 2), "time_limit_s": time_limit,
            "solver": "HiGHS via scipy.optimize.milp", "variables": nv, "constraints": len(rows)}
    if res.x is None:
        info["status"] = "no feasible decision found (time limit)" if res.status == 1 else f"solver status {res.status}"
        return None, info
    x = res.x
    asg = [0] * n
    for i in range(n):
        asg[i] = max(mod.P[i], key=lambda m: x[idx[("z", i, m)]])
    per = [[] for _ in range(mod.M)]
    for i in range(n):
        per[asg[i]].append((x[idx[("s", i)]], i))
    seq = [[i for _, i in sorted(l)] for l in per]
    ev = mod.evaluate(asg, seq)
    sched = mod.to_schedule(asg, ev[2], ev[3])
    gap = getattr(res, "mip_gap", None)
    bound = getattr(res, "mip_dual_bound", None)
    info.update({"status": "optimal" if res.status == 0 else "time limit (not proven optimal)",
                 "makespan": ev[0], "lower_bound": bound, "gap": gap})
    return sched, info
