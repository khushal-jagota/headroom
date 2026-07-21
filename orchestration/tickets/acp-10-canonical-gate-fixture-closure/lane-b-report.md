# Lane B — Worker-manifest fixtures

The five failing Worker-manifest/registry files were brought to the already-delivered backend
contract. Exploration and initiative-planning expected manifests now carry `hermes`; the probe
manifest carries its exact `probe-backend`; registry validation reconstructions reuse the installed
catalog; and the legacy table fixture declares/inserts its already-used `employee_backend`. Strict
manifest equality remains. No production source changed.

Focused result:

```text
.venv/bin/python -m pytest -q <the five Lane B files>
27 passed, 1 warning

.venv/bin/ruff check <the five Lane B files>
All checks passed!

git diff --check -- <the five Lane B files>
PASS
```

No full `./verify`, server, browser, package, generated-asset, docs, memory, or unrelated file action
was performed.
