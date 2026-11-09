"""Read and validate an instance directory (schema version 4). Never edits input files.

Raises InputError (message names file, data row, column) for malformed input.
A valid instance may still be infeasible (e.g. an operation with no eligible machine);
that is reported by `infeasibility_evidence`, not by an exception.
"""
import csv
import json
import os
from dataclasses import dataclass, field


class InputError(Exception):
    pass


# Built-in column spec (type, nullable). schema.json, if present, overrides the types.
SPEC = {
    "jobs.csv": [("job_id", "string"), ("release_time", "integer"),
                 ("due_date", "integer"), ("weight", "integer")],
    "operations.csv": [("operation_id", "string"), ("job_id", "string"),
                       ("sequence", "integer"), ("machine_type", "string")],
    "machine_alternatives.csv": [("operation_id", "string"), ("machine_id", "string"),
                                 ("processing_time", "integer")],
    "machines.csv": [("machine_id", "string"), ("machine_type", "string"),
                     ("available_start", "integer"), ("available_end", "integer")],
}
# Schema version 4 (October 2026) removed disruptions.csv.  Any other file in the directory is ignored.


@dataclass
class Instance:
    name: str
    job_ids: list
    release: dict
    due: dict
    weight: dict
    op_ids: list                 # in file order
    op_job: dict
    op_seq: dict
    op_type: dict
    job_ops: dict                # job_id -> [op_id in sequence order]
    alt: dict                    # op_id -> {machine_id: processing_time}
    machine_ids: list
    machine_type: dict
    avail_start: dict
    avail_end: dict
    warnings: list = field(default_factory=list)      # non-fatal oddities in the input, never silently fixed


def _schema_types(path, fname):
    p = os.path.join(path, "schema.json")
    types = {c: (t, False) for c, t in SPEC[fname]}
    if os.path.exists(p):
        try:
            with open(p) as fh:
                sc = json.load(fh)
            for col in sc["files"][fname]["columns"]:
                types[col["name"]] = (col["type"], bool(col.get("nullable", False)))
        except (OSError, KeyError, ValueError):
            pass   # schema.json is documentation; fall back to the built-in spec
    return types


def _parse(value, typ, fname, row, col):
    where = f"{fname}, data row {row} (file line {row + 1}), column '{col}'"
    v = value.strip()
    if typ == "string":
        return v
    try:
        if typ == "integer":
            return int(v)
        return float(v)
    except ValueError:
        raise InputError(f"{where}: value {value!r} is not a valid {typ}")


def _read(path, fname):
    fp = os.path.join(path, fname)
    if not os.path.isfile(fp):
        raise InputError(f"{fname}: required file is missing")
    types = _schema_types(path, fname)
    with open(fp, newline="") as fh:
        rd = csv.DictReader(fh)
        header = rd.fieldnames or []
        for col, _ in SPEC[fname]:
            if col not in header:
                raise InputError(f"{fname}: required column '{col}' is missing")
        rows = []
        for i, raw in enumerate(rd, start=1):
            row = {}
            for col, _ in SPEC[fname]:
                typ, nullable = types.get(col, ("string", False))
                cell = raw.get(col)
                if cell is None or cell.strip() == "":
                    raise InputError(f"{fname}, data row {i} (file line {i + 1}), column "
                                     f"'{col}': value is empty but the column is required")
                row[col] = _parse(cell, typ, fname, i, col)
            row["_row"] = i
            rows.append(row)
    return rows


def _err(fname, row, col, msg):
    raise InputError(f"{fname}, data row {row['_row']} (file line {row['_row'] + 1}), "
                     f"column '{col}': {msg}")


def load_instance(path):
    if not os.path.isdir(path):
        raise InputError(f"{path}: input directory does not exist")
    jobs = _read(path, "jobs.csv")
    ops = _read(path, "operations.csv")
    alts = _read(path, "machine_alternatives.csv")
    macs = _read(path, "machines.csv")
    warnings = []

    # --- jobs
    release, due, weight, job_ids = {}, {}, {}, []
    for r in jobs:
        if r["job_id"] in release:
            _err("jobs.csv", r, "job_id", f"duplicate job_id {r['job_id']!r}")
        if r["release_time"] < 0:
            _err("jobs.csv", r, "release_time", "must be nonnegative")
        if r["due_date"] < 0:
            _err("jobs.csv", r, "due_date", "must be nonnegative")
        if r["weight"] <= 0:
            _err("jobs.csv", r, "weight", "must be positive")
        job_ids.append(r["job_id"])
        release[r["job_id"]], due[r["job_id"]], weight[r["job_id"]] = \
            r["release_time"], r["due_date"], r["weight"]

    # --- machines
    mtype, a_start, a_end, machine_ids = {}, {}, {}, []
    for r in macs:
        m = r["machine_id"]
        if m in mtype:
            _err("machines.csv", r, "machine_id", f"duplicate machine_id {m!r}")
        if r["available_start"] < 0:
            _err("machines.csv", r, "available_start", "must be nonnegative")
        if r["available_start"] >= r["available_end"]:
            _err("machines.csv", r, "available_end", "must be later than available_start")
        machine_ids.append(m)
        mtype[m], a_start[m], a_end[m] = r["machine_type"], r["available_start"], r["available_end"]

    # --- operations
    op_ids, op_job, op_seq, op_type, by_job = [], {}, {}, {}, {}
    for r in ops:
        o = r["operation_id"]
        if o in op_job:
            _err("operations.csv", r, "operation_id", f"duplicate operation_id {o!r}")
        if r["job_id"] not in release:
            _err("operations.csv", r, "job_id", f"job {r['job_id']!r} does not exist in jobs.csv")
        op_ids.append(o)
        op_job[o], op_seq[o], op_type[o] = r["job_id"], r["sequence"], r["machine_type"]
        by_job.setdefault(r["job_id"], []).append((r["sequence"], o, r))
    job_ops = {}
    for j in job_ids:
        lst = sorted(by_job.get(j, []), key=lambda t: t[0])
        if not lst:
            raise InputError(f"operations.csv: job {j!r} has no operations")
        for k, (seq, o, r) in enumerate(lst, start=1):
            if seq != k:
                _err("operations.csv", r, "sequence",
                     f"job {j!r} sequences must run 1, 2, 3, ... without gaps or repeats "
                     f"(expected {k}, found {seq})")
        job_ops[j] = [o for _, o, _ in lst]

    # --- alternatives
    alt = {o: {} for o in op_ids}
    for r in alts:
        o, m = r["operation_id"], r["machine_id"]
        if o not in op_job:
            _err("machine_alternatives.csv", r, "operation_id", f"operation {o!r} does not exist")
        if m not in mtype:
            _err("machine_alternatives.csv", r, "machine_id", f"machine {m!r} does not exist")
        if r["processing_time"] <= 0:
            _err("machine_alternatives.csv", r, "processing_time", "must be positive")
        if mtype[m] != op_type[o]:
            # The brief defines eligibility by what machine_alternatives.csv lists, so this is accepted,
            # but reported: the data dictionary says listed machines always have the required type.
            warnings.append(f"machine_alternatives.csv, data row {r['_row']}: machine {m!r} has type "
                            f"{mtype[m]!r} but operation {o!r} requires {op_type[o]!r}; the listing is used as is")
        if m in alt[o]:
            _err("machine_alternatives.csv", r, "machine_id",
                 f"duplicate pair ({o!r}, {m!r}); the file has one row per eligible pair")
        alt[o][m] = r["processing_time"]

    return Instance(os.path.basename(os.path.normpath(path)), job_ids, release, due, weight,
                    op_ids, op_job, op_seq, op_type, job_ops, alt, machine_ids, mtype,
                    a_start, a_end, warnings)


def infeasibility_evidence(inst):
    """Return a sentence naming proof that no feasible decision exists, or None."""
    for o in inst.op_ids:
        if not inst.alt[o]:
            return (f"operation {o} (job {inst.op_job[o]}, machine type {inst.op_type[o]}) "
                    f"has no eligible machine in machine_alternatives.csv")
    return None
