# Flexible job shop scheduler (OM 522, option 2)

Authors: Abigail Carl, Hannah Hamilton, and Daniel Thompson

The goal of this project is to design code to minimize the makespan of operations for the given data set. The way for this to be done is using the tabu method. Along with the tabu method, is the tabu_tw. This is the tabu method, but with the additional goal of reducing tardiness. To the best of our knowledge, both of these are fully functional and should work in edge cases as well.

## 1\. The decision

**Owner:** workshop supervisor. **Action:** for every operation of every job, choose an eligible machine and a start time.
**Horizon:** the jobs currently in the shop (tens of operations); re-run when the job list changes.
**Constraints:** each operation runs once, uninterrupted, on a machine listed for it in `machine\\\\\\\\\\\\\\\_alternatives.csv`; a job's
first operation starts at or after its release and each later one after the previous operation completes; no overlap on a
machine and everything inside the machine's available interval.
**Primary measure:** makespan. **Secondary:** total weighted tardiness (TWT). Every comparison reports computation time.
(`summary.json` and the checker also carry on-time fraction and machine utilization, which match `worked-example/metrics.json`
but are not required by the brief.) **Deterministic assumptions:** processing times, releases, due dates and machine availability
are exact.

## 2\. Run

```
python solve.py <input\\\\\\\\\\\\\\\_dir> <output\\\\\\\\\\\\\\\_dir>                  # default: tabu search + tardiness phase, slack 0, seed 1
python solve.py <input\\\\\\\\\\\\\\\_dir> <output\\\\\\\\\\\\\\\_dir> --method rule    # method 1: earliest-start dispatching (rule to beat)
python solve.py <input\\\\\\\\\\\\\\\_dir> <output\\\\\\\\\\\\\\\_dir> --method tabu    # makespan-only tabu search
python solve.py <input\\\\\\\\\\\\\\\_dir> <output\\\\\\\\\\\\\\\_dir> --method milp    # exact HiGHS benchmark, small instances
python solve.py ... --slack 0.06                          # allow +6% makespan for lower tardiness
python check.py <input\\\\\\\\\\\\\\\_dir> <decision.json>               # independent checker
python -m unittest discover -s tests                      # 30 tests
python make\\\\\\\\\\\\\\\_scenarios.py <instance> 30                    # scenarios/<instance>/S001.json ...
python run\\\\\\\\\\\\\\\_experiment.py <instance>                       # stress test (+ optional replanning), resumable
python analyze.py <instance>                              # tables, paired comparisons, plot
python compare.py <instance>; python compare\\\\\\\\\\\\\\\_tw.py <instance> 1 5   # deterministic comparison (instance name, not path)
```

`decision.json` = `{"schedule": \\\\\\\\\\\\\\\[{operation\\\\\\\\\\\\\\\_id, machine\\\\\\\\\\\\\\\_id, start, completion}, ...]}`, times unrounded. `summary.txt/json` hold
status, measures and computation time. Malformed input -> message with file, data row and column, exit 2, no decision file.
Provably infeasible (edge case) -> `infeasible: <evidence>`, exit 0. Valid but nothing found -> `no feasible decision found`,
exit 0. Every decision is re-checked by `fjs/checker.py` before it is written.
**Input validation:** required files and columns, declared types (from `schema.json`), non-empty required cells, identifiers
that exist, positive processing times; also unique identifiers, route sequences 1, 2, 3 ..., nonnegative times, positive
weights, availability intervals that start before they end. A listed machine whose type differs from the operation's is
accepted with a warning (the brief defines eligibility by the listing). Extra files in an instance directory are ignored.

## 3\. Methods

1. **Method 1, the rule to beat: earliest-start dispatching** (`fjs/dispatch.py`), verbatim from the brief. Reproduces the worked
example exactly (62 minutes, identical schedule, all `metrics.json` values).
2. **Researched method: tabu search on the disjunctive graph** (`fjs/tabu.py`).
*Representation:* machine assignment + operation order per machine; start times by a longest-path pass; a cyclic graph is
infeasible and discarded. *Start:* best of four dispatch schedules (the rule and three Giffler-Thompson variants).
*Phase 1 (makespan):* swap adjacent critical operations; move a critical operation to another eligible machine near where
it could start; objective (makespan, TWT) lexicographic; tabu forbids undoing a swap or returning an operation to the machine
it left unless the move beats the best; 5000 iterations; restart from the best solution (perturbed) after 150 non-improving
iterations. *Phase 2 (tardiness, default):* from the phase-1 best, minimize (TWT, makespan) subject to makespan <= (1 + slack)
x the phase-1 makespan, with moves drawn from the tight chains leading to tardy jobs; 2500 iterations; stops early at zero
tardiness. With slack 0 the makespan equals the phase-1 makespan, so phase 2 can only lower TWT. Fixed seed (default 1);
stopping depends on iteration counts, with phase 1 and phase 2 sharing one 240 s wall-clock cap as a safety net. Both
phases report their iteration count and stopping reason.
3. **Exact benchmark: MILP** (`fjs/milp.py`, HiGHS via scipy), big-M model; proves optimality on small cases, found no schedule
for the 128-operation case in 200 s.

Source for the researched method: Brandimarte (1993) 

## 4\. Deterministic comparison (every decision re-checked; 5 seeds for randomized methods, mean \[range])

Computation time is wall-clock for the method on one shared core of the development sandbox (load and check excluded).

|instance (ops)|rule: makespan / TWT / time|tabu makespan-only: makespan / TWT / time|tabu + tardiness, slack 0 (default)|slack 0.06|MILP|
|-|-|-|-|-|-|
|hand\_checkable (10)|62 / 0 / <0.01 s|62 / 0 / 0.5 s|62 / 0 / 0.5 s|62 / 0|62, optimal|
|development\_small (13)|79 / 0 / <0.01 s|79 / 0 / <0.1 s|79 / 0 / <0.1 s|79 / 0|79, optimal|
|development\_medium (45)|141 / 465 / <0.01 s|120 / 335 / 3.9 s|120 / 332 \[322, 338] / 7.4 s|123 / 249 / 7.3 s|120, optimal, 22 s|
|development\_large (128)|339 / 3651 / <0.01 s|284.8 \[280, 290] / 7978 / 27.7 s|284.8 \[280, 290] / 2991 \[2512, 3361] / 81.5 s|298 \[289, 303] / 2182 / 78.7 s|none in 200 s|

On the two smallest cases the rule is already optimal. The makespan-only search wins on makespan but on the large case makes
tardiness worse than the rule (7978 vs 3651); the tardiness phase fixes that at no makespan cost and ends below the rule
(2991). Slack 0.06 lowers TWT further for about 13 extra minutes of makespan.

## 5\. Stress test: processing-time uncertainty (required by the brief)

**Scenarios** (`fjs/scenarios.py`; files in `scenarios/<instance>/`, written before any method runs; 30 per instance; seed
2026 x 100000 + index, independent of the methods): each operation's processing time is multiplied by one independent lognormal
factor with mean 1 and CV 0.2 (measured mean 0.997, sd 0.200 over 1350 draws medium; 0.998 and 0.199 over 3840 large), the same
factor on every machine the operation could use. Each file holds every realized processing time; all policies read the same files.
**Fixed-plan execution** (`fjs/execution.py`): keep every assignment and every machine's order; start each operation as soon as
its machine is free and its job predecessor is done (or its release), using realized times. An independent checker
(`check\\\\\\\\\\\\\\\_realized`) validated every realized schedule. **Fixed plans compared:** the rule (method 1), makespan-only tabu, and the
default tabu + tardiness plan, each built once from nominal data. Observation = one scenario (n = 30); percentiles are across those
30 scenarios.

|medium (45 ops)|nominal|mean makespan|p90|mean TWT|p90 TWT|
|-|-|-|-|-|-|
|Fixed-Rule|141|149.6|160.9|585|770|
|Fixed-Tabu|120|131.0|138.8|491|646|
|Fixed-TabuTW|120|129.5|138.1|436|553|

|large (128 ops)|nominal|mean makespan|p90|mean TWT|p90 TWT|
|-|-|-|-|-|-|
|Fixed-Rule|339|355.9|371.3|4430|4800|
|Fixed-Tabu|290|306.0|314.9|9572|10432|
|Fixed-TabuTW|290|303.0|311.2|3877|4506|

Paired by scenario (first minus second, 95% bootstrap interval over scenarios, 10,000 resamples, seed 7):

* Fixed-TabuTW vs Fixed-Rule, makespan: -20.1 \[-22.4, -17.8] medium and -52.8 \[-57.7, -48.1] large; the tabu plan was better in
30 of 30 scenarios on both. TWT: -149 \[-175, -123] and -553 \[-707, -393].
* Noise costs both about the same: nominal to mean realized makespan is +9.5 (tabu) vs +8.6 (rule) medium, +13.0 vs +16.9 large.
* Makespan-only tabu is fragile on due dates at the large size: its TWT is +5142 \[+4908, +5371] above the rule's. The tardiness
phase removes this (TabuTW vs Tabu -5695 \[-5903, -5480]), so the tardiness phase should stay on.

## 6\. Optional extension: replanning

Trigger: an operation completes more than 5% of the reference horizon H (5.0 min medium, 12.6 large) later than the current plan
expected; at most 3 replans per scenario. At the trigger, started operations are frozen and the rest are re-planned by the same
method on a sub-instance built only from known data (nominal times for unstarted operations, actual completions for finished
ones, nominal-minus-worked for operations in progress). A test confirms unrevealed durations cannot reach the planner. Budget:
400 tabu iterations (+300 tardiness iterations), seed 1.

* Replan-TabuTW vs Fixed-TabuTW makespan: -2.1 \[-3.4, -0.8] medium, -7.0 \[-10.4, -3.5] large; TWT -27 \[-56, +0] and +76 \[-111, +273],
so no reliable tardiness benefit. Replan-Rule vs Fixed-Rule: -0.6 \[-2.8, +0.8] medium (none), -11.0 \[-15.7, -6.2] large.
* Stability cost: about 3.1 of 45 and 15.1 of 128 operations change machine per scenario, and operations starting more than
10 minutes from plan rise from 5.5 to 7.4 and 32.6 to 45.3. Mean planning time 0.7 s medium, 3.6 s large; 2.4 and 1.9 replans
per scenario on average.

## 7\. Recommendation

Supervisor: build the plan with tabu search + tardiness phase (slack 0) and execute it as planned. Against the earliest-start
rule, under lognormal processing-time noise with CV 0.2 (mean across 30 scenarios): about 20 fewer minutes of makespan on a
45-operation shop (149.6 to 129.5) and 53 fewer on a 128-operation shop (355.9 to 303.0), with lower weighted tardiness (585 to 436;
4430 to 3877). Tradeoff: planning takes about 8 s and 80 s instead of well under a second. Replanning gives a
small extra gain in finishing time at the cost of moving 3 to 15 operations to other machines, so it is optional. Reconsider if
duration variability is clearly above CV 0.2, if machines can fail, or if the count of late jobs matters more than weighted
tardiness (on the large instance the default plan has fewer jobs on time than the rule, 0.381 vs 0.516, in the supplementary
on-time measure).

## 8\. Known limitations and open work

* Large-case optimality unknown (simple lower bound 252.5, best found 280).
* One noise level (CV 0.2) and one distribution; releases, due dates and availability are not varied; the stress test ran on the
medium and large instances, not small.
* The code cannot convert non-numeric start times to numeric start times. i.e two vs 2





## 9\. AI and other tool disclosure



* Both Claude and Codex were used for coding. The ai struggled to code the true limit for the time spent on tabu\_tw. The AI allowed non-numeric start times to be considered valid and returned feasible when it should not. Last, the AI allowed machines to start after a job was no longer available.
* Vimeo was used to record
* Visual Studio Code was used to make the code easier to read.

&#x20;



