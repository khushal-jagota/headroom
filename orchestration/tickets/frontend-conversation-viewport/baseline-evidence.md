# Characterization baseline

Recorded before any production-file edit.

## Worktree, import, environment, status, and generated assets

Command:

```sh
git rev-parse --show-toplevel
.venv/bin/python -c 'import planner; print(planner.__file__)'
env | rg '^PLAN_(DB_PATH|LOGS_DIR|DISPATCHER_LOCK_PATH|SERVER_CONTROL_SOCKET|HERMES_HOME)=' || true
git status --short --untracked-files=all
find web/dist/assets -maxdepth 1 -type f -print | sort
```

Exit code: `0`

Full output:

```text
/Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-viewport
/Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-viewport/src/planner/__init__.py
?? orchestration/tickets/frontend-conversation-viewport/contract-lock.md
?? orchestration/tickets/frontend-conversation-viewport/implementation-plan.md
?? orchestration/tickets/frontend-conversation-viewport/plan-review-2.md
?? orchestration/tickets/frontend-conversation-viewport/plan-review-disposition.md
?? orchestration/tickets/frontend-conversation-viewport/plan-review.md
?? orchestration/tickets/frontend-conversation-viewport/ticket.md
web/dist/assets/index-BLYhu5uk.js
web/dist/assets/index-D_OL2IiV.css
web/dist/assets/newsreader-latin-400-normal-BFBkh4jY.woff2
web/dist/assets/newsreader-latin-400-normal-gRTjlS2D.woff
web/dist/assets/newsreader-latin-500-normal-B66TYsaK.woff2
web/dist/assets/newsreader-latin-500-normal-DFwuUcdu.woff
web/dist/assets/newsreader-latin-600-normal-30OJ_TG_.woff2
web/dist/assets/newsreader-latin-600-normal-DUnT2r2g.woff
web/dist/assets/newsreader-latin-ext-400-normal-DYA1XoQK.woff
web/dist/assets/newsreader-latin-ext-400-normal-svq1FPys.woff2
web/dist/assets/newsreader-latin-ext-500-normal-BNHmvKvI.woff2
web/dist/assets/newsreader-latin-ext-500-normal-CZruMFou.woff
web/dist/assets/newsreader-latin-ext-600-normal-BXv5iMHi.woff2
web/dist/assets/newsreader-latin-ext-600-normal-BrbfzHZ5.woff
web/dist/assets/newsreader-vietnamese-400-normal-BekUZro8.woff
web/dist/assets/newsreader-vietnamese-400-normal-DdKr49mV.woff2
web/dist/assets/newsreader-vietnamese-500-normal-BEAbKU8A.woff
web/dist/assets/newsreader-vietnamese-500-normal-CL6a8tp2.woff2
web/dist/assets/newsreader-vietnamese-600-normal-CVAR0otO.woff
web/dist/assets/newsreader-vietnamese-600-normal-CaH84vfx.woff2
```

No matching `PLAN_...` environment variables were printed.

## Production build

Command:

```sh
npm --prefix web run build
```

Exit code: `0`

Full output:

```text

> build
> vite build

vite v6.4.3 building for production...

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 549 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                                0.78 kB │ gzip:   0.40 kB
dist/assets/newsreader-vietnamese-400-normal-DdKr49mV.woff2    5.43 kB
dist/assets/newsreader-vietnamese-500-normal-CL6a8tp2.woff2    5.44 kB
dist/assets/newsreader-vietnamese-600-normal-CaH84vfx.woff2    5.50 kB
dist/assets/newsreader-vietnamese-400-normal-BekUZro8.woff     6.97 kB
dist/assets/newsreader-vietnamese-500-normal-BEAbKU8A.woff     7.04 kB
dist/assets/newsreader-vietnamese-600-normal-CVAR0otO.woff     7.06 kB
dist/assets/newsreader-latin-ext-400-normal-svq1FPys.woff2    14.21 kB
dist/assets/newsreader-latin-ext-500-normal-BNHmvKvI.woff2    15.07 kB
dist/assets/newsreader-latin-ext-600-normal-BXv5iMHi.woff2    15.17 kB
dist/assets/newsreader-latin-ext-400-normal-DYA1XoQK.woff     18.38 kB
dist/assets/newsreader-latin-ext-500-normal-CZruMFou.woff     19.30 kB
dist/assets/newsreader-latin-ext-600-normal-BrbfzHZ5.woff     19.39 kB
dist/assets/newsreader-latin-400-normal-BFBkh4jY.woff2        22.48 kB
dist/assets/newsreader-latin-500-normal-B66TYsaK.woff2        23.62 kB
dist/assets/newsreader-latin-600-normal-30OJ_TG_.woff2        23.88 kB
dist/assets/newsreader-latin-400-normal-gRTjlS2D.woff         28.29 kB
dist/assets/newsreader-latin-500-normal-DFwuUcdu.woff         29.64 kB
dist/assets/newsreader-latin-600-normal-DUnT2r2g.woff         29.78 kB
dist/assets/index-D_OL2IiV.css                                26.08 kB │ gzip:   4.20 kB
dist/assets/index-BLYhu5uk.js                                482.62 kB │ gzip: 152.18 kB
✓ built in 1.14s
```

## Structural conversation-pane harness

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Exit code: `0`

Full output:

```text
conversation-pane.test.mjs: all assertions passed
```

## Browser characterization

Command:

```sh
.venv/bin/pytest -q tests/e2e/test_dev_conversation_pane.py tests/e2e/test_conversation_three_states.py
```

Exit code: `0`

Full output:

```text
..............                                                           [100%]
```
