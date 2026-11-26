"""Stop the rehearsal home by its recorded launcher PID only (never a pattern), and report
whether one SIGTERM stopped every rehearsal process and closed the port.
Usage: python3 -I stop_copy.py REHEARSAL_ROOT PORT OUT_JSON"""
import json, os, signal, socket, sys, time
root, port, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
assert port != 8767
pid = int(open(os.path.join(root, "launcher.pid")).read())

def rehearsal_processes():
    """Processes whose executable lives inside the rehearsal root (the copy's sealed Node)."""
    found = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            exe = os.readlink(f"/proc/{p}/exe")
            cmd = open(f"/proc/{p}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if exe.startswith(root + "/"):
            found.append({"pid": int(p), "cmd": cmd[:200]})
    return found

def listening():
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()

before = rehearsal_processes()
cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode()
assert root in cmd or "foreground_main" in cmd, cmd
started = time.perf_counter()
os.kill(pid, signal.SIGTERM)
supervisor_exit = None
while time.perf_counter() - started < 90:
    if not os.path.exists(f"/proc/{pid}") or open(f"/proc/{pid}/stat").read().split()[2] == "Z":
        supervisor_exit = time.perf_counter() - started
        break
    time.sleep(0.1)
exits = {}
alive = {p["pid"] for p in before}
while time.perf_counter() - started < 90:
    now_alive = {p["pid"] for p in rehearsal_processes()}
    for gone in alive - now_alive:
        exits.setdefault(gone, time.perf_counter() - started)
    alive = now_alive
    if not alive and not listening():
        break
    time.sleep(0.1)
result = {"signalled_pid": pid, "signalled_cmd": cmd[:200], "processes_before": before,
          "supervisor_exit_seconds": supervisor_exit, "all_stopped_seconds": time.perf_counter() - started,
          "exit_seconds_by_pid": {str(k): v for k, v in exits.items()}, "remaining_processes": rehearsal_processes(), "port_listening": listening()}
result["stopped_first_time"] = not result["remaining_processes"] and not result["port_listening"]
json.dump(result, open(out, "w"), indent=1)
print(json.dumps(result, indent=1))
