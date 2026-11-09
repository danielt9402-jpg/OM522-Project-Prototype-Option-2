"""python check.py <input_dir> <decision.json>  -> prints feasibility, violations and recomputed measures."""
import json, sys
from fjs.loader import load_instance
from fjs.checker import check_file
inst = load_instance(sys.argv[1])
rep = check_file(inst, sys.argv[2])
print("FEASIBLE" if rep["feasible"] else "INFEASIBLE")
for v in rep["violations"]:
    print("  violation:", v)
if rep["measures"]:
    print(json.dumps(rep["measures"], indent=1))
sys.exit(0 if rep["feasible"] else 1)
