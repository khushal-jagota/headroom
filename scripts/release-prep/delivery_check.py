"""Send one owner message each to the Chief, a Manager and an idle Ticket on the COPY, through the
web app's own owner routes on loopback, then read each outcome by its ID.
Usage: python3 -I delivery_check.py PORT TARGETS_JSON OUT_JSON   (targets: [{kind,id,conversation_id}])"""
import json, sys, time, uuid, urllib.request, urllib.error, urllib.parse
port, targets_file, out = sys.argv[1:4]
assert port != "8767", "never the live port"
base = f"http://127.0.0.1:{port}/api"
TEXT = "Release rehearsal delivery check on a private copy. No action is needed."

def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method,
        data=None if body is None else json.dumps(body).encode(), headers={"content-type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r), time.perf_counter() - started
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null"), time.perf_counter() - started
        except ValueError:
            return e.code, raw.decode(errors="replace")[:300], time.perf_counter() - started

def send_path(t):
    if t["kind"] == "chief":
        return "/chief/conversation/send"
    return f"/{'managers' if t['kind'] == 'manager' else 'tickets'}/{t['id']}/conversation/send"

def outcome(r):
    q = urllib.parse.urlencode({"owner_kind": r["kind"], "owner_id": r["id"]})
    status, body, _ = call("GET", f"/conversation/owner-sends/{r['message_id']}?{q}")
    return {"http": status, "body": body}

results = []
for t in json.load(open(targets_file)):
    mid = str(uuid.uuid4())
    body = {"conversation_id": t["conversation_id"], "content": [{"piece": "text", "text": TEXT}],
            "sender_label": "Owner", "mode": "steer", "sender_message_id": mid,
            "sent_at_unix_milliseconds": int(time.time() * 1000)}
    status, answer, seconds = call("POST", send_path(t), body)
    results.append({**t, "message_id": mid, "http": status, "answer": answer, "answer_seconds": seconds})
for wait, label in ((5, "outcome_after_5s"), (55, "outcome_after_60s")):
    time.sleep(wait)
    for r in results:
        r[label] = outcome(r)
json.dump(results, open(out, "w"), indent=1)
print(json.dumps(results, indent=1))
