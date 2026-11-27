"""Compare two live_snapshot.py outputs. Usage: python3 -I compare_live.py BEFORE AFTER OUT_JSON"""
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
changed = sorted(k for k in set(a["skills"]) | set(b["skills"]) if a["skills"].get(k) != b["skills"].get(k))
units = lambda s: sorted(l for l in s["user_units"] if l.startswith("panels-live") or l.startswith("panels-executor") or l.startswith("panels-db-backup") or l.startswith("panels-maintenance"))
out = {"skill_entries_before": len(a["skills"]), "skill_entries_after": len(b["skills"]), "changed_skill_entries": changed,
       "command_unchanged": a["command"] == b["command"], "command": b["command"],
       "routes_unchanged": a["routes"] == b["routes"], "live_health_before": a["health"], "live_health_after": b["health"],
       "live_units_unchanged": units(a) == units(b), "live_units_after": units(b)}
json.dump(out, open(sys.argv[3], "w"), indent=1)
print(json.dumps(out, indent=1))
