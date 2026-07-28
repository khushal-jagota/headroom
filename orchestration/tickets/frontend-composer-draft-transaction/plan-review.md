# Implementation-plan review

## Standards

**Verdict: pass, with one gate-list correction.**

The plan otherwise follows the repository's structure and execution rules: it starts with
the locked pure contract, uses focused TDD, leaves browser/resource effects in the Svelte
composition root, avoids visual pass-through fragmentation, rebuilds `web/dist`, and
reserves the single canonical `./verify` run for the settled frontend program.

### Non-blockers

- The settled gate list adds
  `tests/e2e/test_ticket_conversation_reply.py`, which is not named by `ticket.md`.
  Remove it so the plan follows `AGENTS.md`'s named, narrowest-proof rule; this does not
  block the source approach.
- The component forecast of 800–850 lines is optimistic. The current send/refusal block
  is about 70 lines, while the integration adds imports and draft read/apply helpers; a
  result nearer 850–880 lines would still satisfy the ticket because this prerequisite is
  explicitly not the final line-count reduction.
- The browser scenario is feasible through the existing generated host, but should be
  appended after assertions that use fixed send indexes, or use a captured send-count
  baseline. This avoids mechanically renumbering unrelated established assertions.

## Spec

**Verdict: revise before implementation.**

The edit order and ownership boundaries match `ticket.md` and `contract-lock.md`.
In particular, the plan preserves `intakeTail`, complete stale-refusal checks, immediate
after-send application, component-owned URL release/focus, sticky backend selection, and
the owner decision that steer retains model and effort until a non-steer consumes them.

### Blockers

- The pure TDD list does not require `carriedRunValues` to preserve the exact supplied
  `RunValues`, including optional `backendKey`, without resolving defaults or consulting
  catalogs. This is an explicit begin-send rule in `contract-lock.md`. Add a focused
  assertion before implementation; the module must copy/preserve the supplied value, not
  derive a replacement.
- The restoration plan says only “restore the text.” The locked behavior restores the
  **sent** text, while begin-send trims the text piece. State explicitly, and test, that
  restoration derives text from the attempt's sent content rather than resurrecting
  unsent leading/trailing whitespace from `draftBeforeSend.text`.

### Non-blockers

- In the rendered steer proof, assert the first callback mode is `steer` and the second is
  `run_when_free` (or `send_now`) as well as asserting retained picks and their later
  clearing. Returning to non-running is a valid way to force `run_when_free`; recording
  the modes makes the owner decision unambiguous.
- The plan's “running Hermes state” must change only generated test-host state. Hermes is
  the existing steer-capable backend; no production delivery availability or catalog
  policy needs to change.
