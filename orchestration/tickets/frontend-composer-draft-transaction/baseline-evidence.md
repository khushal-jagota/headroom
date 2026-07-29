# Baseline evidence

Baseline revision: `8c13d66699504c59f939bb2bea9f15efd64e8b43`

No production or test source had changed. The only untracked files were this ticket's
planning artifacts.

## Worktree resolution

```text
git root:
/Users/khushaljagota/Coding/planning-v2-worktrees/frontend-composer-draft-transaction

planner import:
/Users/khushaljagota/Coding/planning-v2-worktrees/frontend-composer-draft-transaction/src/planner/__init__.py
```

No `PLAN_DB_PATH`, `PLAN_LOGS_DIR`, `PLAN_DISPATCHER_LOCK_PATH`,
`PLAN_SERVER_CONTROL_SOCKET`, or `PLAN_HERMES_HOME` override was present.

## Starting size

```text
899 web/src/components/conversation/ConversationComposer.svelte
```

## Existing rendered component harness

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Exit code: `0`

```text
conversation-pane.test.mjs: all assertions passed
```

## Svelte and TypeScript diagnostics

Command:

```sh
npm --prefix web run check
```

Exit code: `0`

```text
svelte-check found 0 errors and 0 warnings
```

## Production build

Command:

```sh
npm --prefix web run build
```

Exit code: `0`

```text
✓ 553 modules transformed.
dist/assets/index-C8ABQvyo.css  26.08 kB │ gzip:   4.20 kB
dist/assets/index-CAMyhuZ7.js  483.73 kB │ gzip: 152.30 kB
✓ built in 1.30s
```

The build reproduced the checked-in bundle without a repository diff.

## Server-backed browser baseline

Command:

```sh
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py \
  tests/e2e/test_ticket_conversation_reply.py
```

Exit code: `0`

```text
......
```
