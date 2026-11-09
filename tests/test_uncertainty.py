"""Tests for scenarios, execution, replanning and the tardiness phase.  python -m unittest discover -s tests"""
import copy
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fjs.loader import load_instance                                    # noqa: E402
from fjs.checker import check, check_realized                           # noqa: E402
from fjs.dispatch import earliest_start_rule                            # noqa: E402
from fjs.execution import finish, worked, run_policy, subinstance       # noqa: E402
from fjs.scenarios import make_scenario, write_scenarios, load_scenarios, horizon   # noqa: E402
from fjs.tabu import tabu_search, tabu_search_tw                        # noqa: E402
import fjs.tabu as tabu_module                                           # noqa: E402
from fjs import cli                                                     # noqa: E402

D = lambda n: os.path.join(ROOT, "data", n)    # noqa: E731


def nominal(inst, breakdowns=()):
    return {"scenario_id": "T", "processing_time": {o: dict(inst.alt[o]) for o in inst.op_ids},
            "breakdowns": list(breakdowns)}


class BreakdownArithmetic(unittest.TestCase):
    def test_pause_and_resume(self):
        self.assertEqual(finish(0, 10, [(4, 9)]), 15)        # 4 min worked, 5 min down, 6 min left
        self.assertEqual(finish(0, 4, [(4, 9)]), 4)          # completes exactly when the machine fails
        self.assertEqual(finish(5, 3, [(4, 9)]), 12)         # cannot start inside the breakdown
        self.assertEqual(finish(0, 10, [(2, 3), (5, 8)]), 14)
        self.assertEqual(worked(0, 7, [(4, 6)]), 5)


class Execution(unittest.TestCase):
    def setUp(self):
        self.inst = load_instance(D("hand_checkable"))
        self.sched = earliest_start_rule(self.inst)

    def test_no_disruption_reproduces_plan(self):
        r = run_policy(self.inst, nominal(self.inst), self.sched)
        self.assertEqual(r["makespan"], 62)
        self.assertEqual((r["start_shifts"], r["machine_changes"]), (0, 0))
        for e in self.sched:
            self.assertEqual(r["start"][e["operation_id"]], e["start"])

    def test_breakdown_pauses_running_operation(self):
        e = self.sched[0]
        bd = {"machine_id": e["machine_id"], "unavailable_start": e["start"] + 1, "unavailable_end": e["start"] + 6}
        sc = nominal(self.inst, [bd])
        r = run_policy(self.inst, sc, self.sched)
        self.assertEqual(r["completion"][e["operation_id"]], e["completion"] + 5)   # keeps worked minutes
        self.assertGreaterEqual(r["makespan"], 62)
        self.assertEqual(check_realized(self.inst, sc, r["start"], r["completion"], r["assignment"]), [])

    def test_realized_checker_rejects_start_inside_breakdown(self):
        e = self.sched[0]
        sc = nominal(self.inst, [{"machine_id": e["machine_id"], "unavailable_start": e["start"],
                                  "unavailable_end": e["start"] + 4}])
        r = run_policy(self.inst, nominal(self.inst), self.sched)       # plan ignoring the breakdown
        self.assertTrue(check_realized(self.inst, sc, r["start"], r["completion"], r["assignment"]))

    def test_realized_checker_rejects_machine_availability_overrun(self):
        e = self.sched[0]
        sc = nominal(self.inst)
        sc["processing_time"][e["operation_id"]][e["machine_id"]] = \
            self.inst.avail_end[e["machine_id"]] - e["start"] + 100
        r = run_policy(self.inst, sc, self.sched)
        violations = check_realized(self.inst, sc, r["start"], r["completion"], r["assignment"])
        self.assertTrue(any("outside availability" in v for v in violations), violations)


class Scenarios(unittest.TestCase):
    def test_deterministic_and_saved_files_match(self):
        inst = load_instance(D("development_medium"))
        self.assertEqual(make_scenario(inst, 3), make_scenario(inst, 3))
        self.assertNotEqual(make_scenario(inst, 3)["duration_factor"], make_scenario(inst, 4)["duration_factor"])
        saved = load_scenarios(os.path.join(ROOT, "scenarios", "development_medium"))
        self.assertGreaterEqual(len(saved), 30)
        self.assertEqual(json.loads(json.dumps(make_scenario(inst, 1), sort_keys=True)), saved[0])

    def test_scenario_is_complete_and_method_free(self):
        inst = load_instance(D("development_small"))
        sc = make_scenario(inst, 1)
        for o in inst.op_ids:
            for m, p in inst.alt[o].items():
                self.assertAlmostEqual(sc["processing_time"][o][m], p * sc["duration_factor"][o])
        self.assertEqual(sc["breakdowns"], [])        # the brief varies processing times only
        self.assertGreater(horizon(inst), 0)


class Replanning(unittest.TestCase):
    def setUp(self):
        self.inst = load_instance(D("development_medium"))
        self.sched = earliest_start_rule(self.inst)
        self.sc = make_scenario(self.inst, 2)
        self.late = 0.05 * horizon(self.inst)

    def run_rp(self, sc, planner=earliest_start_rule):
        return run_policy(self.inst, sc, self.sched, planner=planner, late_threshold=self.late)

    def test_no_late_operation_means_no_replan(self):
        fixed = run_policy(self.inst, nominal(self.inst), self.sched)
        rp = self.run_rp(nominal(self.inst))
        self.assertEqual(rp["replans"], 0)
        self.assertEqual(rp["makespan"], fixed["makespan"])

    def test_frozen_operations_never_change_and_result_is_valid(self):
        fixed = run_policy(self.inst, self.sc, self.sched)
        rp = self.run_rp(self.sc)
        self.assertTrue(1 <= rp["replans"] <= 3)
        t1 = rp["replan_times"][0]
        for o in self.inst.op_ids:
            if fixed["start"][o] <= t1:          # started by the first trigger: frozen
                self.assertEqual(rp["start"][o], fixed["start"][o])
                self.assertEqual(rp["assignment"][o], fixed["assignment"][o])
        self.assertEqual(check_realized(self.inst, self.sc, rp["start"], rp["completion"], rp["assignment"]), [])

    def test_planner_never_sees_unrevealed_data(self):
        """Inflate the realized durations of operations that have not started at the first trigger: the
        sub-instance handed to the planner at that trigger must be identical."""
        seen = []
        def spy(sub):
            seen.append((sub.release, sub.avail_start, sub.alt, sub.op_ids))
            return earliest_start_rule(sub)
        base = self.run_rp(self.sc, spy)
        first_call, t1 = seen[0], base["replan_times"][0]
        fx = run_policy(self.inst, self.sc, self.sched)
        sc2 = copy.deepcopy(self.sc)
        for o in self.inst.op_ids:
            if fx["start"][o] > t1:
                for m in sc2["processing_time"][o]:
                    sc2["processing_time"][o][m] *= 1.7
        seen.clear()
        self.run_rp(sc2, spy)
        self.assertEqual(seen[0], first_call)

    def test_replanning_with_breakdowns_is_rejected(self):
        sc = nominal(self.inst, [{"machine_id": self.inst.machine_ids[0], "unavailable_start": 5, "unavailable_end": 9}])
        with self.assertRaises(ValueError):
            self.run_rp(sc)

    def test_subinstance_respects_ready_times(self):
        remaining = set(self.inst.op_ids[:6])
        sub = subinstance(self.inst, remaining, {j: 50.0 for j in self.inst.job_ids},
                          {m: 70.0 for m in self.inst.machine_ids})
        s = earliest_start_rule(sub)
        self.assertTrue(all(e["start"] >= 50 for e in s))


class TardinessPhase(unittest.TestCase):
    def test_two_phase_search_shares_time_budget_and_reports_both_stops(self):
        inst = load_instance(D("hand_checkable"))
        original_search = tabu_module._search
        budgets = []

        def capture_budget(*args, **kwargs):
            budgets.append(args[8])
            return original_search(*args, **kwargs)

        with patch("fjs.tabu._search", side_effect=capture_budget), \
             patch("fjs.tabu.time.time", side_effect=[10.0, 15.0, 20.0, 25.0]):
            _, info = tabu_search_tw(inst, iterations=0, tw_iterations=0, time_cap=30.0)

        self.assertEqual(budgets, [30.0, 20.0])
        self.assertEqual(info["phase1_stop_reason"], "iteration limit")
        self.assertEqual(info["phase2_stop_reason"], "iteration limit")

    def test_makespan_cap_and_tardiness_never_worse(self):
        inst = load_instance(D("development_medium"))
        for slack in (0.0, 0.05):
            s, info = tabu_search_tw(inst, slack=slack, seed=1, iterations=600, tw_iterations=400)
            rep = check(inst, {"schedule": s})
            self.assertTrue(rep["feasible"], rep["violations"])
            m = rep["measures"]
            self.assertLessEqual(m["makespan"], info["phase1_makespan"] * (1 + slack) + 1e-9)
            self.assertLessEqual(m["total_weighted_tardiness"], info["phase1_twt"])
            if slack == 0.0:
                self.assertEqual(m["makespan"], info["phase1_makespan"])

    def test_phase1_unchanged_by_refactor(self):
        inst = load_instance(D("development_medium"))
        s, _ = tabu_search(inst, seed=1, iterations=5000, stall=150)
        self.assertEqual(check(inst, {"schedule": s})["measures"]["makespan"], 120)

    def test_default_cli_method_is_checked_and_exported(self):
        out = tempfile.mkdtemp()
        rc = cli.run(D("hand_checkable"), out, out=open(os.devnull, "w"))
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(out, "decision.json")))


if __name__ == "__main__":
    unittest.main()
