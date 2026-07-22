# Panels runtime model and Hermes home

Use this when the user asks which model Panels workers/Chief are using, whether Panels sets one explicitly, or why old tickets still appear to run on another model.

## Current shape

Panels does not currently have a separate `worker_model` or `chief_model` setting.

At server startup, `src/planner/core/server.py` creates Hermes gateway children through `SharedGateway`:

- the shared worker gateway uses the configured worker role skill, usually `panels-worker`;
- the Chief gateway uses `panels-chief-of-staff`;
- both use the Hermes home resolved by `planner.minds.config.resolve_planner_home()`.

`resolve_planner_home()` defaults to `data/hermes-home` and can be overridden by `PLAN_HERMES_HOME`.

`SharedGateway._env()` sets `HERMES_HOME`, `HERMES_TUI_SKILLS`, and `HERMES_PYTHON_SRC_ROOT` for the child. It does not set a model environment variable. Model/provider selection therefore comes from the Hermes config in that home, usually `data/hermes-home/config.yaml`.

## How to inspect

From the Panels repo root:

```sh
HERMES_HOME=data/hermes-home hermes config show
```

or read only the relevant YAML:

```sh
python3 - <<'PY'
from pathlib import Path
import yaml, json
p = Path('data/hermes-home/config.yaml')
data = yaml.safe_load(p.read_text()) or {}
print(json.dumps(data.get('model'), indent=2))
PY
```

Useful source files:

- `config.yaml` — Panels app config; has `worker_skill`, `gateway_adapter`, but no model field.
- `src/planner/core/config.py` — parses Panels config/env overrides.
- `src/planner/minds/config.py` — resolves `PLAN_HERMES_HOME` and Hermes Python.
- `src/planner/core/server.py` — wires worker and Chief gateways.
- `src/planner/minds/shared_gateway.py` — passes `HERMES_HOME` and `HERMES_TUI_SKILLS` to Hermes.

## Nuance: new sessions vs existing sessions

Changing the Hermes home's main model affects new sessions. Existing durable Hermes sessions can keep the model they started with unless they are explicitly switched in-session, for example with `/model`.

Logs may therefore show a mix: newer sessions using the current `data/hermes-home/config.yaml` model, while older ticket sessions continue on an earlier model.

## If explicit per-role control is desired

That is not just config today. It would need new Panels machinery, such as `worker_model` / `chief_model` config fields and gateway/session creation logic that switches or pins the model for those roles.

Until that exists, treat Panels worker/Chief model choice as Hermes-home-level default plus per-existing-session state.
