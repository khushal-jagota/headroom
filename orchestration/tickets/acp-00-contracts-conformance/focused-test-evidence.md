# ACP-00 focused test evidence

Date: `2026-07-19`  
Working directory: `/Users/khushaljagota/.hermes/planning-v2`

The commands below are the exact focused sequence from `plan.md`, rerun after accepting all three
findings in `implementation-review-round-1.md`. All corrected settled-tree commands exited with
status 0. `./verify` was not run.

## Python dependency install

```text
$ .venv/bin/python -m pip install -r requirements.txt
Requirement already satisfied: annotated-doc==0.0.4 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 1)) (0.0.4)
Requirement already satisfied: annotated-types==0.7.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 2)) (0.7.0)
Requirement already satisfied: agent-client-protocol==0.11.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 3)) (0.11.0)
Requirement already satisfied: anyio==4.14.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 4)) (4.14.1)
Requirement already satisfied: ast_serialize==0.6.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 5)) (0.6.0)
Requirement already satisfied: certifi==2026.6.17 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 6)) (2026.6.17)
Requirement already satisfied: charset-normalizer==3.4.7 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 7)) (3.4.7)
Requirement already satisfied: click==8.4.2 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 8)) (8.4.2)
Requirement already satisfied: fastapi==0.139.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 9)) (0.139.0)
Requirement already satisfied: greenlet==3.5.3 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 10)) (3.5.3)
Requirement already satisfied: h11==0.16.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 11)) (0.16.0)
Requirement already satisfied: httpcore==1.0.9 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 12)) (1.0.9)
Requirement already satisfied: httpx==0.28.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 13)) (0.28.1)
Requirement already satisfied: idna==3.18 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 14)) (3.18)
Requirement already satisfied: iniconfig==2.3.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 15)) (2.3.0)
Requirement already satisfied: librt==0.12.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 16)) (0.12.0)
Requirement already satisfied: mypy==2.1.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 17)) (2.1.0)
Requirement already satisfied: mypy_extensions==1.1.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 18)) (1.1.0)
Requirement already satisfied: packaging==26.2 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 19)) (26.2)
Requirement already satisfied: pathspec==1.1.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 20)) (1.1.1)
Requirement already satisfied: playwright==1.61.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 21)) (1.61.0)
Requirement already satisfied: pluggy==1.6.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 22)) (1.6.0)
Requirement already satisfied: pydantic==2.13.4 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 23)) (2.13.4)
Requirement already satisfied: pydantic_core==2.46.4 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 24)) (2.46.4)
Requirement already satisfied: pyee==13.0.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 25)) (13.0.1)
Requirement already satisfied: Pygments==2.20.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 26)) (2.20.0)
Requirement already satisfied: pytest==9.1.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 27)) (9.1.1)
Requirement already satisfied: pytest-base-url==2.1.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 28)) (2.1.0)
Requirement already satisfied: pytest-playwright==0.8.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 29)) (0.8.0)
Requirement already satisfied: python-slugify==8.0.4 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 30)) (8.0.4)
Requirement already satisfied: PyYAML==6.0.3 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 31)) (6.0.3)
Requirement already satisfied: requests==2.34.2 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 32)) (2.34.2)
Requirement already satisfied: ruff==0.15.20 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 33)) (0.15.20)
Requirement already satisfied: starlette==1.3.1 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 34)) (1.3.1)
Requirement already satisfied: text-unidecode==1.3 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 35)) (1.3)
Requirement already satisfied: types-PyYAML==6.0.12.20260518 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 36)) (6.0.12.20260518)
Requirement already satisfied: typing-inspection==0.4.2 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 37)) (0.4.2)
Requirement already satisfied: typing_extensions==4.16.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 38)) (4.16.0)
Requirement already satisfied: urllib3==2.7.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 39)) (2.7.0)
Requirement already satisfied: uvicorn==0.50.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 40)) (0.50.0)
Requirement already satisfied: websockets==16.0 in ./.venv/lib/python3.14/site-packages (from -r requirements.txt (line 41)) (16.0)

[notice] A new release of pip is available: 26.0 -> 26.1.2
[notice] To update, run: /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pip install --upgrade pip
```

## Browser dependency install

```text
$ npm --prefix web install

added 3 packages, and audited 52 packages in 596ms

9 packages are looking for funding
  run `npm fund` for details

found 0 vulnerabilities
```

## Ruff

```text
$ .venv/bin/ruff check src/planner/conversation tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py tests/support/acp_*.py
All checks passed!
```

## Mypy

```text
$ .venv/bin/mypy src/planner/conversation
Success: no issues found in 5 source files
```

## Focused Python tests

```text
$ .venv/bin/pytest tests/unit/test_conversation_contracts.py tests/unit/test_acp_conformance_harness.py
..................................................................       [100%]
66 passed in 0.72s
```

## Direct TypeScript contract test

```text
$ node web/tests/acp-contracts.test.mjs
acp-contracts.test.mjs: all assertions passed
```

## Svelte/TypeScript check

```text
$ npm --prefix web run check

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/.hermes/planning-v2/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

## Web test script

```text
$ npm --prefix web test

> test
> node tests/resource-catalogue.test.mjs && node tests/resource-cache.test.mjs && node tests/ws-connection.test.mjs && node tests/neutral-pane.test.mjs && node tests/ticket-neutral-pane.test.mjs && node tests/file-preview.test.mjs && node tests/lifecycle.test.mjs && node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/chat-images.test.mjs && node tests/acp-contracts.test.mjs

resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
[planner] ws open since=0
[planner] flush 1
[planner] ws open since=1
ws-connection.test.mjs: all assertions passed
neutral-pane.test.mjs: all assertions passed
ticket-neutral-pane.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
acp-contracts.test.mjs: all assertions passed
```
