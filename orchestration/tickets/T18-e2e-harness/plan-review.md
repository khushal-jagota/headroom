# T18 plan review — codex exec output + orchestrator dispositions

Reviewer: `codex exec` pointed at plan.md, ticket.md, SPEC §18.2/18.3 (items 22–27), and the
ground-truth source/asset files the plan cites. Verdict: **PLAN NEEDS CHANGES** (2 findings,
both accepted; item-27 interpretation endorsed; no owned-file violations found).

## Codex findings (verbatim substance)

1. plan.md §A server_factory opens `<srvdir>/server.log`, but the plan never creates `srvdir`
   after defining it as `tmp_path / f"srv{n}"`. `plan serve` creates DB/log dirs later, inside
   the subprocess (cli/main.py:152), so the parent-side log open can fail before boot. Add
   `srvdir.mkdir(parents=True)` before opening the log.

2. plan.md test 26 claims the chat planner-message scan "survives" the `chat_session_created`
   flush. Not guaranteed. Chat messages are only client module state (components.js:333); the
   server persists/emits the session event before returning the send response
   (chat/service.py:81); the client only repaints the planner reply if the old panel is still
   connected (components.js:671-675). If the WS flush replaces the panel before the fetch
   callback runs, the wait for `[data-chat-msg="planner"]` can time out. Not covered by the
   wsOpens gate or the catch-up settle.

3. Item 27: agrees with the plan's three-CLI-proposal interpretation — SPEC item 27's "two CLI
   proposals advance it" is only consistent if success+approach are the advancing proposals and
   the plan proposal is a third that parks at the ceiling (SPEC.md:306, SPEC.md:280,
   tests/unit/test_tickets_engine.py:113). No outside-owned-file touches found.

## Orchestrator dispositions

1. **ACCEPT.** Trivial and real: the fixture writes `<srvdir>/server.log` from the parent
   process before the subprocess exists; `tmp_path` exists but `srv{n}` does not.
   → Amendment A1: `srvdir.mkdir(parents=True, exist_ok=True)` before opening the log file.

2. **ACCEPT.** Verified against the code: components.js:673 skips the reply `paint()` when a
   flush re-render detached the panel mid-flight, and no later event would ever trigger a
   repaint (chat/service.py emits `chat_session_created` on the FIRST send only; subsequent
   sends emit nothing). The race window is small (HTTP reply vs 50 ms WS poll + 50 ms
   debounce) but nonzero — exactly the flake class the ticket forbids.
   → Amendment A2: test 26's echo half sends TWO messages. The first ("warmup") mints the
   session and triggers the one-and-only chat flush; the test waits for that flush to land
   and settle, asserts `chat_session_key == "fake-sess-1"` via API, then sends the asserted
   message ("hello from e2e"). No event follows a subsequent send, so no re-render can race
   the reply paint — the planner echo assertion is deterministic. SPEC item 26's letter is
   satisfied by the second sent message rendering its reply.

3. **ENDORSED, no change.** The three-proposal reading stands as written in the plan and is
   now double-confirmed (plan's own derivation + codex against unit item 3's implementation).

Both amendments are appended to plan.md as a binding amendments section; the implementer
builds to plan.md as amended.
