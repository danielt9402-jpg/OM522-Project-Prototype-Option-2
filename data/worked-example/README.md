# Worked example for option 2

Scheduling jobs with different operation routes.

This example applies the option's rule to beat (defined in option-2-brief.pdf) to ../hand_checkable/. It shows one feasible decision and how every measure except computation time is recomputed from that decision. It is not an optimal answer. The rule to beat is the team's first method, and the team's researched method must outperform this rule by more than 0.001 on the instructor's held-out case. solution.json has the structure that a team's decision.json must use. metrics.json is a reference for testing, not a required output, and its key names are not graded (e.g., makespan is the final or project completion time, and on_time_rate is the on-time fraction). Counts such as jobs_completed are consistency checks, not additional measures. The JSON files keep full numeric precision, and the tables below round to three decimals. All times are in minutes.

The rule looks at the next unscheduled operation of every job. For each operation and each eligible machine, the possible start is the later of the machine becoming free and the job's previous operation finishing (or its release). It schedules the pair with the earliest start, breaking ties by earliest completion, then operation identifier, then machine identifier, and repeats.

## Decisions

| Operation id | Machine id | Start | Completion |
| --- | --- | --- | --- |
| J002_O1 | DRILL_1 | 0 | 10 |
| J003_O1 | CUT_2 | 4 | 9 |
| J001_O1 | MILL_2 | 6 | 16 |
| J003_O2 | DRILL_2 | 9 | 36 |
| J002_O2 | MILL_2 | 16 | 22 |
| J001_O2 | CUT_2 | 16 | 40 |
| J002_O3 | FINISH_1 | 22 | 36 |
| J003_O3 | MILL_1 | 36 | 48 |
| J001_O3 | DRILL_2 | 40 | 53 |
| J003_O4 | FINISH_2 | 48 | 62 |

## Recomputed measures

| Measure | Value |
| --- | ---: |
| Final completion time | 62 |
| Total weighted tardiness | 0 |
| On-time fraction | 1.000 |
| Jobs completed | 3 |
| Machine utilization (processing minutes divided by makespan) | CUT_1 0.000; CUT_2 0.468; CUT_3 0.000; DRILL_1 0.161; DRILL_2 0.645; FINISH_1 0.226; FINISH_2 0.226; MILL_1 0.194; MILL_2 0.258 |

The on-time fraction is between 0 and 1, not a percentage.
