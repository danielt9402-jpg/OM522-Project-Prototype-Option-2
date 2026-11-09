"""Run with:  python -m pytest -q   (or  python -m unittest discover -s tests)"""
import csv
import json
import os
import random
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fjs.loader import load_instance, InputError, infeasibility_evidence   # noqa: E402
from fjs.checker import check                                                # noqa: E402
from fjs.dispatch import earliest_start_rule                                 # noqa: E402
from fjs.tabu import tabu_search                                             # noqa: E402
from fjs import cli                                                          # noqa: E402

D = lambda name: os.path.join(ROOT, "data", name)   # noqa: E731


def copy_instance(name):
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, name)
    shutil.copytree(D(name), dst)
    return tmp, dst


def rewrite(path, fn):
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    rows = fn(rows)
    with open(path, "w", newline="") as fh:
        csv.writer(fh).writerows(rows)


class HandCheckable(unittest.TestCase):
    """Expected values come from worked-example/, computed independently by the instructor."""

    def test_rule_reproduces_worked_example_schedule(self):
        inst = load_instance(D("hand_checkable"))
        got = {(e["operation_id"], e["machine_id"], e["start"], e["completion"])
               for e in earliest_start_rule(inst)}
        want_json = json.load(open(D("worked-example/solution.json")))
        want = {(e["operation_id"], e["machine_id"], e["start"], e["completion"])
                for e in want_json["schedule"]}
        self.assertEqual(got, want)

    def test_checker_recomputes_worked_example_measures(self):
        inst = load_instance(D("hand_checkable"))
        rep = check(inst, json.load(open(D("worked-example/solution.json"))))
        ref = json.load(open(D("worked-example/metrics.json")))
        self.assertTrue(rep["feasible"], rep["violations"])
        m = rep["measures"]
        self.assertEqual(m["makespan"], ref["makespan"])
        self.assertEqual(m["total_weighted_tardiness"], ref["total_weighted_tardiness"])
        self.assertAlmostEqual(m["on_time_fraction"], ref["on_time_rate"])
        for k, v in ref["machine_utilization"].items():
            self.assertAlmostEqual(m["machine_utilization"][k], v)


class CheckerRejects(unittest.TestCase):
    def setUp(self):
        self.inst = load_instance(D("hand_checkable"))
        self.good = json.load(open(D("worked-example/solution.json")))

    def mutate(self, op, **kw):
        d = json.loads(json.dumps(self.good))
        for e in d["schedule"]:
            if e["operation_id"] == op:
                e.update(kw)
        return d

    def test_each_constraint(self):
        cases = {
            "overlap on machine": self.mutate("J001_O2", start=9, completion=33),      # CUT_2 vs J003_O1 [4,9)... shift
            "starts before release": self.mutate("J001_O1", start=5, completion=15),
            "before predecessor": self.mutate("J002_O2", start=15, completion=21),
            "ineligible machine": self.mutate("J002_O2", machine_id="MILL_1"),
            "wrong duration": self.mutate("J002_O2", completion=23),
        }
        # overlap case needs a genuinely overlapping pair: put J003_O3 on top of J001_O3's DRILL_2 slot
        cases["overlap on machine"] = self.mutate("J003_O3", machine_id="MILL_2", start=16, completion=30)
        for label, dec in cases.items():
            with self.subTest(label):
                self.assertFalse(check(self.inst, dec)["feasible"], label)

    def test_missing_and_duplicate_operation(self):
        d = json.loads(json.dumps(self.good))
        d["schedule"].pop()
        self.assertFalse(check(self.inst, d)["feasible"])
        d = json.loads(json.dumps(self.good))
        d["schedule"].append(d["schedule"][0])
        self.assertFalse(check(self.inst, d)["feasible"])

    def test_time_zero_machine_availability(self):
        d = self.mutate("J002_O1", start=-3, completion=7)
        self.assertFalse(check(self.inst, d)["feasible"])

    def test_non_finite_times_are_rejected(self):
        for field, value in (("start", float("nan")), ("completion", float("inf"))):
            with self.subTest(field=field):
                d = self.mutate("J002_O1", **{field: value})
                self.assertFalse(check(self.inst, d)["feasible"])


class InputHandling(unittest.TestCase):
    def test_edge_case_is_infeasible_exit_zero_no_decision(self):
        out = tempfile.mkdtemp()
        rc = cli.run(D("edge_case"), out, out=open(os.devnull, "w"))
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(out, "decision.json")))
        self.assertIn("infeasible", open(os.path.join(out, "summary.txt")).read())
        self.assertIn("J003_O4", infeasibility_evidence(load_instance(D("edge_case"))))

    def test_malformed_inputs_name_file_row_column(self):
        def bad(file, fn, expect):
            tmp, dst = copy_instance("hand_checkable")
            rewrite(os.path.join(dst, file), fn)
            with self.assertRaises(InputError) as cm:
                load_instance(dst)
            for token in expect:
                self.assertIn(token, str(cm.exception))
            rc = cli.run(dst, os.path.join(tmp, "out"), err=open(os.devnull, "w"))
            self.assertNotEqual(rc, 0)
            self.assertFalse(os.path.exists(os.path.join(tmp, "out", "decision.json")))

        def drop_col(rows):
            i = rows[0].index("weight")
            return [r[:i] + r[i + 1:] for r in rows]
        bad("jobs.csv", drop_col, ["jobs.csv", "weight"])
        bad("machine_alternatives.csv",
            lambda r: [r[0]] + [[r[1][0], r[1][1], "-4"]] + r[2:], ["machine_alternatives.csv", "row 1", "processing_time"])
        bad("machine_alternatives.csv",
            lambda r: [r[0]] + [[r[1][0], r[1][1], "abc"]] + r[2:], ["processing_time", "not a valid integer"])
        bad("operations.csv", lambda r: [x if x[0] != "J001_O3" else [x[0], x[1], "5", x[3]] for x in r], ["sequence", "J001"])
        bad("operations.csv", lambda r: r[:1] + [["J001_O1", "J999", "1", "MILL"]] + r[2:], ["job_id", "J999"])
        bad("machines.csv", lambda r: [r[0]] + [[r[1][0], r[1][1], "50", "10"]] + r[2:], ["available_end"])

    def test_schema_v4_has_no_disruptions_file_and_old_extras_are_ignored(self):
        tmp, dst = copy_instance("hand_checkable")
        self.assertFalse(os.path.exists(os.path.join(dst, "disruptions.csv")))
        load_instance(dst)                                          # loads without it
        with open(os.path.join(dst, "disruptions.csv"), "w") as fh:  # an older package's extra file is ignored
            fh.write("scenario_id,machine_id,unavailable_start,unavailable_end\nX,NOPE,5,1\n")
        self.assertEqual(len(load_instance(dst).op_ids), 10)

    def test_machine_type_mismatch_is_a_warning_not_an_error(self):
        tmp, dst = copy_instance("hand_checkable")
        rewrite(os.path.join(dst, "machine_alternatives.csv"),
                lambda r: [r[0]] + [[r[1][0], "FINISH_1", r[1][2]]] + r[2:])     # a machine of another type
        inst = load_instance(dst)
        self.assertEqual(len(inst.warnings), 1)
        self.assertIn("requires", inst.warnings[0])

    def test_missing_file(self):
        tmp, dst = copy_instance("hand_checkable")
        os.remove(os.path.join(dst, "machines.csv"))
        with self.assertRaises(InputError) as cm:
            load_instance(dst)
        self.assertIn("machines.csv", str(cm.exception))

    def test_row_order_and_identifier_matching(self):
        base = earliest_start_rule(load_instance(D("development_small")))
        tmp, dst = copy_instance("development_small")
        rnd = random.Random(7)
        for f in ("jobs.csv", "operations.csv", "machine_alternatives.csv", "machines.csv"):
            rewrite(os.path.join(dst, f), lambda r: [r[0]] + rnd.sample(r[1:], len(r) - 1))
        shuffled = earliest_start_rule(load_instance(dst))
        self.assertEqual(max(e["completion"] for e in base), max(e["completion"] for e in shuffled))


class Regression(unittest.TestCase):
    RULE = {"hand_checkable": 62, "development_small": 79, "development_medium": 141, "development_large": 339}

    def test_rule_makespans_do_not_change(self):
        for name, want in self.RULE.items():
            with self.subTest(name):
                inst = load_instance(D(name))
                rep = check(inst, {"schedule": earliest_start_rule(inst)})
                self.assertTrue(rep["feasible"])
                self.assertEqual(rep["measures"]["makespan"], want)

    def test_tabu_feasible_deterministic_and_beats_rule(self):
        for name, ceiling in (("hand_checkable", 62), ("development_small", 79), ("development_medium", 123)):
            with self.subTest(name):
                inst = load_instance(D(name))
                s1, _ = tabu_search(inst, seed=1, iterations=1500)
                s2, _ = tabu_search(inst, seed=1, iterations=1500)
                self.assertEqual(s1, s2)                                   # fixed seed reproduces the run
                rep = check(inst, {"schedule": s1})
                self.assertTrue(rep["feasible"], rep["violations"])
                self.assertLessEqual(rep["measures"]["makespan"], ceiling)
        inst = load_instance(D("development_medium"))
        s, _ = tabu_search(inst, seed=1, iterations=1500)
        self.assertLess(check(inst, {"schedule": s})["measures"]["makespan"], 141 - 0.001)


if __name__ == "__main__":
    unittest.main()
