"""Restart the COPY's home the way a real update's restart looks to an open page: an attempt record in
state 'installing' WITH a deadline_at (as live's update records carry), then stop by the recorded PID
(stop_copy.py), wait like the catch-up, relaunch (launch_copy.sh), wait for ready, then set the record aside.
Records wall-clock times so a page watcher (restart_watch.mjs) can be lined up.
Usage: SHA=<full sha> [UNIT_PREFIX=...] python3 -I restart_with_deadline.py REHEARSAL_ROOT APP PORT SCRIPTS_DIR OUT_JSON"""
import datetime, json, os, subprocess, sys, time, urllib.request
root, app, port, scripts, out = sys.argv[1:6]
assert port != "8767"
SHA = os.environ["SHA"]
data = f"{root}/data"
record = f"{data}/update-attempt.json"
wall = {}

def ready(timeout=300):
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health?expected_sha={SHA}", timeout=5) as r:
                if json.load(r).get("ready"):
                    return time.perf_counter() - started
        except Exception:
            pass
        time.sleep(0.25)
    return None

subprocess.run(["python3", "-I", f"{scripts}/prepare_attempt.py", data, "rehearsal-deadline-restart", SHA, SHA, f"{root}/release.json"], check=True, capture_output=True)
r = json.load(open(record))
r["deadline_at"] = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)).isoformat()
json.dump(r, open(record, "w"), indent=1)
wall["record_written"] = time.time()
time.sleep(15)  # let the open page read the update state
wall["stop_start"] = time.time()
stop = json.loads(subprocess.run(["python3", "-I", f"{scripts}/stop_copy.py", root, port, f"{os.path.dirname(out)}/stop-for-deadline-restart.json"],
                                 capture_output=True, text=True, check=True).stdout)
wall["stopped"] = time.time()
time.sleep(4)  # about the measured catch-up
wall["relaunch"] = time.time()
subprocess.run([f"{scripts}/launch_copy.sh", root, app, port], check=True, capture_output=True)
ready_seconds = ready()
wall["ready"] = time.time()
time.sleep(20)
os.replace(record, f"{root}/attempt-after-deadline-restart.json")
wall["record_set_aside"] = time.time()
result = {"wall": wall, "stop": {k: stop[k] for k in ("signalled_pid", "all_stopped_seconds", "stopped_first_time", "port_listening")},
          "ready_seconds_after_relaunch": ready_seconds, "launcher_pid_after": int(open(f"{root}/launcher.pid").read())}
json.dump(result, open(out, "w"), indent=1)
print(json.dumps(result, indent=1))
