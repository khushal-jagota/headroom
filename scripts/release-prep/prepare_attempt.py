"""Write a private update attempt record for the copy, so snapshot entry points record into it.
Usage: python3 -I prepare_attempt.py DATA_DIR ATTEMPT_ID FROM_SHA TO_SHA [RELEASE_JSON]
RELEASE_JSON: a release object so the serving home can parse the record (the old-flow run had none)."""
import json, os, sys, datetime
data, attempt, from_sha, to_sha = sys.argv[1:5]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
path = os.path.join(data, "update-attempt.json")
json.dump({"attempt_id": attempt, "from_sha": from_sha, "to_sha": to_sha, "mode": "now",
           "state": "installing", "created_at": now, "updated_at": now, "detail": None,
           "snapshot_required": True, "steps": {"rehearsal_prepared": now},
           **({"release": json.load(open(sys.argv[5]))} if len(sys.argv) > 5 else {})}, open(path, "w"), indent=1)
open(path + ".lock", "a").close()
print(path)
