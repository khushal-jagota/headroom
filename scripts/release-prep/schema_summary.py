"""Split schema_timed events into each step's apply, scoped FK check, seed check, commit, and scoped integrity check.
Usage: python3 -I schema_summary.py EVENTS_JSON OUT_JSON"""
import json, sys
d = json.load(open(sys.argv[1]))
ev = sorted(d["events"], key=lambda e: e["start"])
steps, cur = [], None
for e in ev:
    sql = e["sql"]
    if e["kind"] == "exec" and sql.startswith("BEGIN IMMEDIATE"):
        cur = {"begin": e["start"], "fk_ms": 0.0, "seed_ms": 0.0, "integrity_ms": 0.0, "integrity_tables": [], "apply_ms": 0.0, "revision": None}
        steps.append(cur)
        continue
    if cur is None:
        continue
    if e["kind"] == "exec" and sql.startswith("COMMIT"):
        cur["commit_end"] = e["start"] + e["ms"]
        cur["commit_ms"] = e["ms"]
        continue
    if "pragma_integrity_check" in sql:
        cur["integrity_ms"] += e["ms"]
        cur["integrity_tables"] += (e.get("args") or [])[:1]
        cur["end"] = e["start"] + e["ms"]
    elif "foreign_key" in sql:
        cur["fk_ms"] += e["ms"]
    elif "UPDATE alembic_version" in sql:
        cur["revision"] = (e.get("args") or [None])[0]
    elif "sqlite_schema" in sql or "sqlite_master" in sql:
        cur["seed_ms"] += e["ms"]
    elif "commit_end" not in cur:
        cur["apply_ms"] += e["ms"]
out = {"exit_code": d["exit_code"], "total_seconds": d["total_ms"] / 1000, "steps": []}
for s in steps:
    end = s.get("end", s.get("commit_end", s["begin"]))
    out["steps"].append({"revision": s["revision"], "step_seconds": (end - s["begin"]) / 1000,
        "apply_seconds": s["apply_ms"] / 1000, "scoped_fk_check_seconds": s["fk_ms"] / 1000,
        "seed_schema_check_seconds": s["seed_ms"] / 1000, "commit_seconds": s.get("commit_ms", 0) / 1000,
        "scoped_integrity_check_seconds": s["integrity_ms"] / 1000, "integrity_tables": s["integrity_tables"]})
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(json.dumps(out, indent=1))
