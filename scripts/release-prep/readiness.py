"""From the recorded launch time: seconds until ready health, and until the Chief's conversation view
answers 200 (not 409 'Conversation view is upgrading') and its tail stream sends a snapshot.
Usage: CANDIDATE_SHA=<full sha> python3 -I readiness.py REHEARSAL_ROOT PORT CHIEF_CONVERSATION_ID OUT_JSON"""
import json, os, sys, time, urllib.request, urllib.error
root, port, chief, out = sys.argv[1:5]
assert port != "8767"
SHA = os.environ["CANDIDATE_SHA"]  # the full candidate SHA
launched = float(open(f"{root}/launch.time").read())
base = f"http://127.0.0.1:{port}"
res = {"view_409_upgrading_seen": 0}

def get(path, timeout=10):
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return None, str(e).encode()

while "health_seconds" not in res and time.time() - launched < 900:
    status, body = get(f"/api/health?expected_sha={SHA}", 5)
    if status == 200 and json.loads(body).get("ready"):
        res["health_seconds"] = time.time() - launched
        res["health"] = json.loads(body)
    else:
        time.sleep(0.2)
while "chief_view_seconds" not in res and time.time() - launched < 900:
    status, body = get(f"/api/conversation/conversations/{chief}/view?lens=focus&transcript=false", 30)
    if status == 200:
        res["chief_view_seconds"] = time.time() - launched
    else:
        if status == 409 and b"upgrading" in body:
            res["view_409_upgrading_seen"] += 1
        time.sleep(0.2)
json.dump(res, open(out, "w"), indent=1)
print(json.dumps(res, indent=1))
