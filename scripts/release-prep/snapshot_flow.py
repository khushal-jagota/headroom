"""The candidate's update snapshot flow on the COPY, in the order the real update runs it:
  1. serving home: snapshot_stage_main.ts (staged copy + full check)  -> snapshot_seconds.staged_copy
     (service.ts times this child and writes it into the attempt; this script does the same)
  2. stop the home (stop_copy.py)
  3. snapshot_main.ts with the staged copy (in-window catch-up)        -> snapshot_seconds.catch_up
  4. start the home again (launch_copy.sh) and wait for ready health
  5. snapshot_verify_main.ts (late check + retention)                  -> snapshot_seconds.late_check/retention
Usage: CANDIDATE_SHA=<full sha> python3 -I snapshot_flow.py REHEARSAL_ROOT APP PORT SCRIPTS_DIR EVIDENCE_DIR"""
import json, os, subprocess, sys, time, urllib.request
root, app, port, scripts, evidence = sys.argv[1:6]
assert port != "8767"
SHA = os.environ["CANDIDATE_SHA"]  # the full candidate SHA
data, db, backups = f"{root}/data", f"{root}/data/planner.db", f"{root}/data/backups"
node, upd = f"{app}/node/bin/node", f"{app}/server/environments/updates"
ATTEMPT = "rehearsal-new-flow"
env = {"PATH": "/usr/bin:/bin", "HOME": f"{root}/home", "TMPDIR": f"{root}/tmp", "LANG": "C.UTF-8"}

def timed(argv):
    started = time.perf_counter()
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    return {"argv": [a.replace(root, "$R") for a in argv], "exit": p.returncode, "seconds": time.perf_counter() - started,
            "stdout": p.stdout[-2000:], "stderr": p.stderr[-4000:]}

def ready(timeout=300):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health?expected_sha={SHA}", timeout=5) as r:
                body = json.load(r)
                if body.get("ready"):
                    return time.perf_counter() - started
        except Exception:
            pass
        time.sleep(0.25)
    return None

steps = {}
wall = {}  # wall-clock epoch seconds, to line up with a page watching the restart
subprocess.run(["python3", "-I", f"{scripts}/prepare_attempt.py", data, ATTEMPT, SHA, "next-release", f"{root}/release.json"], check=True, env=env)
steps["stage"] = timed([node, f"{upd}/snapshot_stage_main.ts", db, backups])
staged = steps["stage"]["stdout"].strip()
record_path = f"{data}/update-attempt.json"
record = json.load(open(record_path))
record["staged_snapshot"] = staged
record["snapshot_seconds"] = {"staged_copy": steps["stage"]["seconds"]}
json.dump(record, open(record_path, "w"), indent=1)
wall["stop_start"] = time.time()
steps["stop"] = json.loads(subprocess.run(["python3", "-I", f"{scripts}/stop_copy.py", root, port, f"{evidence}/stop-for-snapshot.json"],
                                          capture_output=True, text=True, check=True).stdout)
wall["stopped"] = time.time()
steps["catch_up"] = timed([node, f"{upd}/snapshot_main.ts", db, backups, SHA, data, ATTEMPT, staged])
wall["relaunch"] = time.time()
subprocess.run([f"{scripts}/launch_copy.sh", root, app, port], check=True)
steps["restart_ready_seconds"] = ready()
wall["ready"] = time.time()
snapshot = json.load(open(record_path)).get("backup")
steps["late_check_and_retention"] = timed([node, f"{upd}/snapshot_verify_main.ts", snapshot, backups, db, data, ATTEMPT])
final = json.load(open(record_path))
# The record stays 'installing' after a rehearsal; the home would then show "Updating" and its
# full-page update pause would block every later check. Set it aside, as a finished update would end it.
os.replace(record_path, f"{root}/attempt-after-snapshot.json")
wall["record_set_aside"] = time.time()
out = {"wall": wall, "steps": steps, "attempt_record": final, "backups_after": sorted(os.listdir(backups))}
text = json.dumps(out, indent=1).replace(root, "$R")
open(f"{evidence}/snapshot-flow.json", "w").write(text)
print(text)
