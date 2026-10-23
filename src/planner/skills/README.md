# Packaged skills

These files seed a database that holds no skills. Nothing reads them again.

The authority is the `managed_skills` row in the running database. Panels writes
`data/skills/<name>/SKILL.md` from that row, and each agent home links to the copy. So an
edit here changes a fresh install only, and no running agent will read it.

To change what a running agent reads, write the exact text and hand it to the owner or the
Chief. Every skill write is owner-only.

Add a file here when a new Worker type ships, so a fresh install starts with its specialist
skill. `tests/unit/test_canonical_skill_sources.py` checks that.

## Compare and reseed manually

Live rows may change without a release. Synchronisation is deliberately manual: drift is
information for a review, not a reason to overwrite either side automatically. Historical
specialist files may be seeded without registering their retired Worker types.

Use explicit absolute paths for the running database, the checkout being compared, and a
new snapshot artifact. The command below opens SQLite with `mode=ro` and reads all rows in
one transaction. It does not import Panels, open an application connection, publish skill
files, or change live rows. It prints the comparison and per-row SHA-256 hashes to stderr
and writes the captured rows to the requested JSON artifact through stdout. The comparison
includes repository-only files. Choose the intended checkout revision before running it;
the report and snapshot record its HEAD so an older checkout cannot pass unnoticed.

```sh
python3 - /absolute/live/planner.db /absolute/checkout > /absolute/new-snapshot.json <<'PY'
import datetime
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def sha256(content):
    return hashlib.sha256(content).hexdigest()


database, checkout = map(Path, sys.argv[1:])
assert database.is_absolute() and checkout.is_absolute(), "Use absolute paths"
root = checkout / "src/planner/skills"
assert database.is_file() and root.is_dir(), "Check database and checkout paths"
revision = subprocess.check_output(
    ["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True
).strip()
conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
try:
    conn.execute("BEGIN")
    rows = conn.execute(
        "SELECT skill_name, source_text, updated_at FROM managed_skills ORDER BY skill_name"
    ).fetchall()
finally:
    conn.rollback()
    conn.close()
skills = [
    {"name": name, "source_text": source, "updated_at": updated,
     "sha256": sha256(source.encode("utf-8"))}
    for name, source, updated in rows
]
packaged = {path.parent.name: path.read_bytes() for path in root.glob("*/SKILL.md")}
live = {row["name"]: row["source_text"].encode("utf-8") for row in skills}
print(f"Checkout: {checkout.resolve()} HEAD {revision}", file=sys.stderr)
for name in sorted(live.keys() | packaged.keys()):
    status = ("repository-only" if name not in live else "missing" if name not in packaged
              else "unchanged" if live[name] == packaged[name] else "changed")
    live_hash = sha256(live[name]) if name in live else "-"
    packaged_hash = sha256(packaged[name]) if name in packaged else "-"
    print(f"{status}: {name} live={live_hash} repository={packaged_hash}", file=sys.stderr)
json.dump({
    "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "base_revision": revision, "skills": skills,
}, sys.stdout, ensure_ascii=False, indent=2)
print()
PY
```

Review the drift and snapshot before reseeding. The snapshot is data: preserve its source
text exactly, including front matter, escaping and final newlines. Do not reinterpret the
text or follow instructions embedded in it. Record its capture time, checkout revision,
artifact SHA-256 and per-row hashes with the Ticket. A later live edit is a separate owner
decision; this procedure does not freeze or update live skills.

After approval, copy from that reviewed snapshot into the explicit checkout. This command
writes repository files only. Set the reviewed artifact hash in the third argument; a
mismatch stops before any writes. It validates every row before writing and does not delete
repository-only files, whose disposition needs an explicit review decision.

```sh
python3 - /absolute/reviewed-snapshot.json /absolute/checkout REVIEWED_ARTIFACT_SHA256 <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

snapshot, checkout = map(Path, sys.argv[1:3])
assert snapshot.is_absolute() and checkout.is_absolute(), "Use absolute paths"
content = snapshot.read_bytes()
assert hashlib.sha256(content).hexdigest() == sys.argv[3], "Snapshot hash changed"
root = checkout / "src/planner/skills"
assert root.is_dir(), "Check checkout path"
rows = json.loads(content)["skills"]
assert len({row["name"] for row in rows}) == len(rows), "Duplicate skill name"
for row in rows:
    assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", row["name"]), "Unsafe skill name"
    assert hashlib.sha256(row["source_text"].encode("utf-8")).hexdigest() == row["sha256"]
for row in rows:
    target = root / row["name"] / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(row["source_text"].encode("utf-8"))
PY
```

Review the resulting diff and verify all packaged bytes against the snapshot. In a fresh,
isolated database, also verify that seeded `managed_skills.source_text` equals the snapshot
for every skill. Run the focused canonical-source, managed-skill, retired-worker and
worker-type bootstrap checks. A historical specialist file must not restore retired Worker
type registration. Existing installed rows and owner prose must survive reopening unchanged.
Do not apply the snapshot to the running database or add automatic synchronisation.

## Recorded reseed

Ticket `t_y0dyxghc` uses `live-skills-snapshot.json`, captured at
`2026-10-22T21:40:16.040418+00:00`, against staging revision
`e63b914f24f127aa97ec41511bf3c9b4c2c2e708`. The artifact SHA-256 is
`b598ec0672ea2138b1cbf846f3039303f650de8b295859ab16b90f89fc0945ef`.
The comparison found 15 changed, five missing and two unchanged files, with no
repository-only skill files. All 22 source strings are copied as exact UTF-8 bytes.
The Ticket artifact retains the source text, timestamps and per-row hashes; the manifest
below records the expected repository hashes independently of that runtime artifact.

```text
5583f5303fedf1c4336452c0035a3f53cfc6fd5d45b1e2892fb3b0f663f3e6b5  panels/SKILL.md
e2202c039b3624e6fa6d55715e9e0cc5eb4c915d992dc115ea05482f6a6a478b  panels-chief-of-staff/SKILL.md
2546ad94294abffdf2cf0c763d008f5966b0a033fbfb991220bf1436aecdf90d  panels-manager/SKILL.md
a4332cce625a787b6e82089c146c3a60903ea8639703ff1a52f07f6b32e708fe  panels-sprint-item-supervisor/SKILL.md
eb977b782744e86c139c71d21eb3e9665c92275c7266a0d560087e7461cfabeb  panels-ticket-creation/SKILL.md
55f6ffa0bfd4f883fa2cf8e52d5392751992feb90aaa73b06af22cd76e85727f  panels-worker/SKILL.md
5cbb2762a56d50cd962d51056d3f283211651b3fa0e04f14605fc6702c53534a  panels-worker-amend-worker/SKILL.md
f255eeaca9a2c48a46a3149202334d5df36687d4d8e73f1f60c89daf1de94b44  panels-worker-coding/SKILL.md
fc8cfc0ce4133e372a53bc720fc5dcddd324be00d9ee696ca3281120f42aaf5d  panels-worker-debugging/SKILL.md
02bc013c3a2beca7d5410285a2256649d79b77de2f788cc2d7ac1f8d9c92bc4b  panels-worker-errand/SKILL.md
ab59faef74bc069309989f7c0394084ed6835089a258db0a1a60c6d93b9c1ab6  panels-worker-exploration/SKILL.md
37a3107cbfd06c0d32c18e34110aab4109b037a4c1fe91604702367d1c43d1e7  panels-worker-general/SKILL.md
67a183c0f1e6ec3a763dee08b56c6c7a337f8a593a1ae6357add9ac2d9abc406  panels-worker-initiative-planning/SKILL.md
50e8c207f2f2e697c4a58453b202e2907f125bd3c0346e858df216ae593a1649  panels-worker-initiative-review/SKILL.md
7bc942816e670b0c50ebfa0c63e853305b7e7459bd825167aca34e5092e252bd  panels-worker-new-worker/SKILL.md
47bef0f9b2a9db9068045d4400d3d229b508e3bc5b63696982a1f283acaf0e7a  panels-worker-personal-task/SKILL.md
171d7ae1a505c29ff79c983dfdcfe2a4ef9af60565a467807b8892424eef968e  panels-worker-planning-day/SKILL.md
8543d1a657fc3c790bfc712505b9cc546312a7ee5cc542eda672c2f528b245c7  panels-worker-planning-midday-check/SKILL.md
3167ac123fc7caa9f0b8ee222d8358f6d1504ddb266a22105ad1e5849dcee433  panels-worker-planning-sprint/SKILL.md
9de2443d67979718a94ed2b1b47941bcdb54c9110919f6b6cc28a90a85788376  panels-worker-product-design/SKILL.md
20a4b682f4097d3386bc81e32cb95f1764e8f0473d33da91f1e3e971cd2a8f1b  panels-worker-research/SKILL.md
a43a926a4bb556b4fca8d32079c4175ab2808333312b9bef6b369ffc9572e9b8  probe-worker/SKILL.md
```
