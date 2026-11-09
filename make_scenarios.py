"""python make_scenarios.py <instance> [count]  -> scenarios/<instance>/S001.json ...
Generated from documented seeds (fjs/scenarios.py) BEFORE any method is run; every method and policy reads these files."""
import sys
from fjs.loader import load_instance
from fjs.scenarios import write_scenarios, horizon
name = sys.argv[1]
count = int(sys.argv[2]) if len(sys.argv) > 2 else 30
inst = load_instance(f"data/{name}")
paths = write_scenarios(inst, f"scenarios/{name}", count)
print(f"{name}: wrote {len(paths)} scenarios to scenarios/{name}/  (reference horizon H = {horizon(inst):.2f})")
