"""Copy live read-only (SQLite online backup + conversation files), then make the copy harmless.
Usage: python3 -I copy_and_sanitize.py REHEARSAL_ROOT EVIDENCE_JSON"""
import json, os, shutil, sqlite3, subprocess, sys, time

LIVE = "/home/vps/Deployments/Panels/current/data"
root, evidence = sys.argv[1], sys.argv[2]
data = os.path.join(root, "data")
os.makedirs(data, mode=0o700)
copy = os.path.join(data, "planner.db")

started = time.perf_counter()
src = sqlite3.connect(f"file:{LIVE}/planner.db?mode=ro", uri=True)
dst = sqlite3.connect(copy)
src.backup(dst)
src.close()
backup_seconds = time.perf_counter() - started

started = time.perf_counter()
shutil.copytree(f"{LIVE}/files/conversations", os.path.join(data, "files", "conversations"), symlinks=True)
files_seconds = time.perf_counter() - started

# Metrics sidecar: copied read-only too, so the candidate's CREATE TABLE IF NOT EXISTS runs on a real existing sidecar.
started = time.perf_counter()
side_src = sqlite3.connect(f"file:{LIVE}/planner.db.vps-metrics.sqlite3?mode=ro", uri=True)
side_dst = sqlite3.connect(copy + ".vps-metrics.sqlite3")
side_src.backup(side_dst)
side_src.close()
sidecar_tables = [r[0] for r in side_dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
side_dst.close()
sidecar_seconds = time.perf_counter() - started

SANITIZE = [
    ("conversation_child_custody", "DELETE FROM conversation_child_custody"),
    ("conversation_background_tasks", "DELETE FROM conversation_background_tasks"),
    ("notification_push_subscriptions", "DELETE FROM notification_push_subscriptions"),
    ("notification_apns_deliveries", "DELETE FROM notification_apns_deliveries"),
    ("notification_live_activities", "DELETE FROM notification_live_activities"),
    ("notification_deliveries", "DELETE FROM notification_deliveries"),
    ("notification_web_push_identity", "DELETE FROM notification_web_push_identity"),
    ("notification_apns_devices", "DELETE FROM notification_apns_devices"),
    ("credentials", "DELETE FROM credentials"),
    ("credential_request_delivery_attempts", "DELETE FROM credential_request_delivery_attempts"),
    ("credential_requests", "DELETE FROM credential_requests"),
    ("agent_launch_tokens", "DELETE FROM agent_launch_tokens"),
    ("machine_executor_credentials", "DELETE FROM machine_executor_credentials"),
    ("mac_executor_presence", "DELETE FROM mac_executor_presence"),
    ("deploy_interruption_deliveries", "DELETE FROM deploy_interruption_deliveries"),
    ("deploy_interruptions_closed", "UPDATE deploy_interruptions SET recovery_state='closed' WHERE recovery_state='awaiting_action'"),
]
removed = {}
with dst:
    for name, sql in SANITIZE:
        removed[name] = dst.execute(sql).rowcount
fk = dst.execute("PRAGMA foreign_key_check").fetchall()
schema = dst.execute("SELECT version_num FROM alembic_version").fetchall()
dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
dst.close()

out = {
    "backup_seconds": backup_seconds,
    "conversation_files_seconds": files_seconds,
    "database_bytes": os.path.getsize(copy),
    "sidecar_seconds": sidecar_seconds,
    "sidecar_bytes": os.path.getsize(copy + ".vps-metrics.sqlite3"),
    "sidecar_tables_before": sidecar_tables,
    "sanitized_rows": removed,
    "foreign_key_violations": len(fk),
    "schema_before": [r[0] for r in schema],
}
json.dump(out, open(evidence, "w"), indent=1)
print(json.dumps(out, indent=1))
