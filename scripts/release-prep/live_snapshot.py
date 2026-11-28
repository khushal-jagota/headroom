"""Read-only snapshot of the protected live setup: skill links, live command, routes, health, units.
Usage: LIVE_SHA=<full sha> python3 -I live_snapshot.py OUT_JSON"""
import hashlib, json, os, shutil, subprocess, sys, urllib.request

LIVE_SHA = os.environ["LIVE_SHA"]  # the full live SHA from the live app manifest
ROOTS = ["~/.claude/skills", "~/.codex/skills", "~/.agents/skills", "~/.gemini/config/skills"]

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def skills():
    out = {}
    for root in ROOTS:
        root = os.path.expanduser(root)
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            for name in dirnames + filenames:
                p = os.path.join(dirpath, name)
                entry = {}
                if os.path.islink(p):
                    entry["link"] = os.readlink(p)
                if os.path.isfile(p):
                    entry["sha256"] = sha(p)
                out[p] = entry
    return out

def run(argv):
    return subprocess.run(argv, capture_output=True, text=True).stdout

def main():
    command = shutil.which("panels")
    with urllib.request.urlopen(f"http://127.0.0.1:8767/api/health?expected_sha={LIVE_SHA}", timeout=10) as r:
        health = json.load(r)
    snap = {
        "skills": skills(),
        "command": {"path": command, "realpath": os.path.realpath(command), "sha256": sha(command)},
        "routes": json.loads(run(["tailscale", "serve", "status", "--json"])),
        "health": health,
        "user_units": run(["systemctl", "--user", "list-units", "--all", "--no-pager", "--plain", "--no-legend"]).splitlines(),
    }
    json.dump(snap, open(sys.argv[1], "w"), indent=1, sort_keys=True)
    print(len(snap["skills"]), "skill entries;", health)

main()
