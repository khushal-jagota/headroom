This package adds one optional Anthropic craft instruction to the product-design Worker.
The existing live Worker text remains byte-for-byte identical outside that addition.
The Worker keeps the approved direction and house style. `assets/tokens.css` takes precedence over all advice about token values.

Chief relayed the owner's revised scope: preserve Chief's direct Anthropic installation and prepare only the Worker update.
Chief installed `frontend-design` at `/home/vps/.local/share/agent-skills/frontend-design` with symlinks in Claude and Codex.
This package needs no Anthropic installation or native provisioning.
It does not change any actual native home.

The repository Worker file seeds fresh databases. Only the owner can apply the live Worker row.
No live row, database, or file under Deployments changed during this package's preparation.

Read [verification.md](verification.md) for skill recommendations and the isolated provisioning evidence.

## Exact sources

- `skill-apply.json` contains the complete reviewed Worker source and its expected current hash.
- `product-design-before.json` records the independently fetched live Worker before the change.
- `sources-and-hashes.json` records scope attribution and source hashes.
- `src/planner/skills/panels-worker-product-design/SKILL.md` is the revised seed in this branch.

The live Worker matched the seed before this change.
The expected old hash is `9de2443d67979718a94ed2b1b47941bcdb54c9110919f6b6cc28a90a85788376`.
The resulting Worker hash is `6bbcd29d30a22da4b20e5bc697204015687e8fad407873b7ae1012a65e348262`.
The manifest changes only `panels-worker-product-design`.

## Owner application

Run this command in the owner's terminal after approval.
Do not run it from a Worker claim or remove its credentials to bypass the owner guard.
The `panels` executable resolves to the current deployment's CLI.
The supported `skill apply` command writes through the owner-only API.

```sh
panels skill apply --input-json /home/vps/projects/panels/task-tmp/t_dfy1ubd5-handoff/skill-apply.json --json
```

If publication fails after the row commit, retry the exact complete manifest:

```sh
panels skill apply --input-json /home/vps/projects/panels/task-tmp/t_dfy1ubd5-handoff/skill-apply.json --json
```

An exact complete retry republishes the source without another logical edit.
If the source hash changed, stop and review fresh live text. Do not replace the expected hash blindly.
No native provisioning command is necessary.

## Verify the live result

```sh
panels skill show panels-worker-product-design --json
panels skill check
python3 - <<'PY'
from pathlib import Path
import hashlib
import json
import subprocess

package = Path('/home/vps/projects/panels/task-tmp/t_dfy1ubd5-handoff')
manifest = json.loads((package / 'skill-apply.json').read_text())
assert len(manifest['skills']) == 1
entry = manifest['skills'][0]
assert entry['name'] == 'panels-worker-product-design'
expected = entry['source_text'].encode('utf-8')
actual = json.loads(subprocess.check_output(
    ['panels', 'skill', 'show', entry['name'], '--json'], text=True,
))['source_text'].encode('utf-8')
assert actual == expected, 'live Worker row differs'
managed = Path('/home/vps/Deployments/Panels/current/data/skills/panels-worker-product-design/SKILL.md')
assert managed.read_bytes() == expected, 'managed Worker copy differs'
print(entry['name'], hashlib.sha256(actual).hexdigest(), 'live row and managed copy verified')
PY
```

Record the command output on the Ticket. Existing conversations need a fresh skill read to receive the added guidance.
Repository integration remains a separate Consequences action through a pull request into `staging`.
