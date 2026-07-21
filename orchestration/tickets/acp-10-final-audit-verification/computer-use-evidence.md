# ACP-10 Computer Use evidence

Date: 2026-07-20–21 (Europe/London)

Verdict: **PASS**. All actions below used the production-served Panels app at
`http://127.0.0.1:8767`. The unstyled Vite page at `5189`, TestClient, Playwright, and direct API
calls are not counted as experiential evidence.

## Final visible state

Computer Use was performed first in Chrome and, for the final post-restart checks, in Safari. The
real app retained Panels' dark split layout: product navigation and Ticket stages remain the primary
surface, while the conversation stays a restrained right rail. Typed thought and tool rows are
collapsed by default; permission is the only prominent blocking inset. This matches the owner-supplied
pre-migration Panels screenshot rather than the bare Vite page seen earlier in the migration.

The final Safari frames were emitted through the Computer Use tool in the implementation thread:

- Codex after restart:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.01.15 am.jpeg`
- Claude after restart:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.01.49 am.jpeg`
- Hermes human -> automatic -> human continuity:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.08.42 am.jpeg`
- Hermes native edit-permission rejection:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.15.10 am.jpeg`
- Codex native permission choices before rejection:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.16.33 am.jpeg`
- Codex permission-rejected outcome:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.16.42 am.jpeg`
- Codex clean Send Now successor:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.21.19 am.jpeg`
- Codex live typed plan:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.22.09 am.jpeg`
- Codex same-session child recovery after the stored-plan probe:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.25.56 am.jpeg`
- Claude queued prompt and disabled Steer:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.27.52 am.jpeg`
- Claude fresh-child Send Now retest after the provider background-task interval:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.51.01 am.jpeg`
- Claude native ExitPlanMode permission choices:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.52.34 am.jpeg`
- Claude permission-approved read-only result:
  `/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/com.openai.sky.CUAService/Safari Screenshot 2026-07-21 at 12.52.54 am.jpeg`

These frames show the normal Ticket editor and proposal card beside the compact conversation rail,
not a separate ACP application.

## Hermes baseline matrix

All rows below were exercised through actual Chrome Computer Use on `8767` during ACP-05 and then
recorded contemporaneously in `PROGRESS.md`.

| Behavior | Visible result | Verdict |
|---|---|---|
| Fresh Chief conversation | `New conversation` created a fresh Hermes ACP binding and the next prompt returned `ACP dogfood ready.` | PASS |
| Typed replay | Thought, tool, terminal, patch/diff, image, permission, usage, command and agent content remained distinct after hard reload | PASS |
| Queue and cancel | A 20-second tool turn kept running while a queued prompt showed a pending Cancel control; cancelling it produced `interrupted · Queued prompt cancelled`, and it never ran | PASS |
| Stop and recovery | Stop rendered `interrupted · Prompt interrupted`; the forbidden old completion never appeared; immediate reuse returned `STOP RECOVERY READY.` on the same durable binding | PASS |
| Send Now | The old turn was visibly interrupted and exactly one successor returned `SEND NOW RECOVERY READY.`; late old-generation output did not enter the successor | PASS |
| Native Steer | An active Hermes turn accepted Steer, showed its acknowledgement, and returned exactly `STEERED READY.` | PASS |
| Permission and diff | The blocking permission card displayed the exact supplied diff and choices; Allow applied the change and returned `PERMISSION DIFF READY` | PASS |
| Permission rejection | On Ticket `t_x2f5up6e`, Hermes requested approval to patch only `data/acp-dogfood-diff.txt` from `delta` to `epsilon`; Safari selected the supplied `Deny` option, the card settled once, Hermes reported `Permission was denied; the file was not modified.`, and the file remained `alpha` / `delta` | PASS |
| Image | Native picker attached the owner-provided screenshot, the transcript showed the image block, and Hermes vision replied `Dark workspace dashboard displaying tasks.` | PASS |
| Child death | Killing the exact idle child produced visible `Employee connection failed`; a replacement child loaded the unchanged binding and replied `RESPAWN READY.` | PASS |
| New conversation | Deliberate replacement cleared the transcript and advanced the binding generation, distinct from child recovery | PASS |
| Ticket pane | A real Ticket rail returned `TICKET CHAT READY.` without disturbing the Ticket editor | PASS |
| Automatic Employee | Disposable Ticket `t_b7sdhtzn` was naturally discovered without a manual worker command, showed typed worker activity, and produced the normal Success proposal/status | PASS |
| Compaction | Explicit compaction showed `Context compacting`, durably advanced the Hermes binding, and then showed one plain `Context compacted · explicit`; hard reload and server restart retained the messages plus the content-free boundary, and the successor session answered `ACP REPLAY CONTINUES 20260720.` | PASS |

The requested-cancel and compaction investigations intentionally exposed real failures before these
passing retests. Their corrections and exact focused evidence live under
`orchestration/tickets/acp-05-*`; the failed attempts are not counted as passes.

ACP-10 closed the one remaining same-Ticket continuity question on fresh Ticket `t_x2f5up6e`. Merely
opening pristine Kickoff left both binding and Ticket session empty. The first human prompt then
created Hermes session `f5b57fd7-7873-4285-9f6c-0e6d0ee219d4`, generation 1, and returned exactly
`HERMES HUMAN OK`. After ordinary Kickoff approval and addition to today, the discovery loop—not a
manual worker command—ran `run_1wcjzguy`, rendered typed skill/thought/terminal activity, and produced
the normal Success proposal. Human chat afterward returned exactly `HERMES AFTER AUTO OK`. The
binding, Ticket mirror, and completed run all retain that exact session ID.

## Codex functional-worker matrix

Ticket: `t_nkq3108b` — **ACP Codex worker dogfood v2 2026-07-20**

1. The ordinary pristine-Kickoff UI showed the backend selector with Codex selected. Read-only DB
   inspection proved no binding or session existed merely because the Ticket had been opened.
2. The first human prompt created Codex ACP session
   `019f81a8-7041-7bb3-b7da-4445912fc3d0`, generation 1, and returned exactly
   `CODEX HUMAN OK`.
3. `/compact` visibly entered `compacting`, showed a content-free in-progress row, completed on the
   same session, and reduced the displayed token usage. Hard refresh retained the content-free
   completion boundary and typed transcript.
4. Kickoff was approved and the Ticket was added to today. The ordinary discovery loop claimed the
   Ticket and ran `run_3k18evvz` on that exact session. The rail showed typed thought and tool rows,
   real permission choices (Allow Once was used), and the normal worker-authored Success proposal.
5. Human chat after the automatic step returned exactly `CODEX AFTER AUTO OK`.
6. During a real 20-second tool turn the composer displayed Steer disabled with its honest
   explanation. Queue was selected, `CODEX QUEUE SECOND COMPLETE` showed `queued · queue 1` with a
   Cancel control, and it started automatically only after `CODEX QUEUE FIRST COMPLETE`; both exact
   answers appeared once and in FIFO order.
7. After a full Panels server restart, Safari loaded the same typed Ticket transcript and returned
   exactly `CODEX RESTART OK`.
8. A later real Safari prompt first showed the sandboxed local `curl` failing, then retried that exact
   command with elevated local-process access. Panels displayed the ordered adapter-supplied choices
   `Allow Once`, `Allow for Session`, the exact command-prefix allowance, and `Reject`; selecting
   `Reject` settled the card once, left the tool failed, and Codex reported `Elevated access was
   declined, so the command was not run.`
9. A clean Send Now probe interrupted an active 60-second tool turn as
   `interrupted · Prompt interrupted`, then returned exactly
   `CODEX SEND NOW CLEAN SUCCESSOR 20260721`. The forbidden predecessor completion did not appear.
10. A native two-step plan rendered live as a typed `Current plan`, with `Observe pinned plan
    rendering` completed and `Confirm accepted prose limitation` in progress. Browser refresh and an
    exact Codex child restart did not reconstruct that typed plan in the browser surface; the same
    generation-1 binding still answered `CODEX CHILD RESTART PLAN LOAD OK`. This is the accepted
    pinned presentation limitation. Panels neither parsed provider prose nor invented a replacement
    plan; the exact adapter-level `Plan:` replay shape remains pinned by qualification/conformance.

Verdict: **PASS** for real human -> naturally discovered Automatic Employee -> human -> server
restart continuity and the full required provider-control matrix through one selected Codex
backend/session.

## Claude Code functional-worker matrix

Ticket: `t_1xbdpkq0` — **ACP Claude worker dogfood 2026-07-20**

1. The ordinary pristine-Kickoff UI selected Claude before any binding existed. The first human
   prompt created Claude ACP session `e2562875-0b3f-482e-8f75-52e0d0e46731`, generation 1, and
   returned exactly `CLAUDE HUMAN OK`.
2. An early `/compact` with insufficient history failed visibly with the exact provider reason
   `Compacting failed: Not enough messages to compact.`; Panels did not turn elapsed time into a
   fabricated failure.
3. The ordinary discovery loop ran `run_jsmxnrt8` on the same session. Typed internal Bash/tool rows
   remained distinct, and the worker produced the normal Success proposal. Ordinary Bash correctly
   produced no human card under the local Claude `auto` permission policy.
4. Human chat after the automatic step returned exactly `CLAUDE AFTER AUTO OK`.
5. A later `/compact` visibly entered `compacting` and completed on the same session. Hard reload
   initially exposed the pinned adapter's private synthetic compact-summary message. Dogfood therefore
   failed that replay claim, added the exact red regression, and fixed only Claude's replay classifier.
   The passing retest now shows one content-free `Context compacted · automatic` row and no summary,
   transcript path, or private instructions.
6. After a full Panels server restart, Safari loaded that corrected content-free replay and returned
   exactly `CLAUDE RESTART OK`.
7. During a real 20-second Bash turn the composer showed Steer disabled and Queue selected. The
   queued `CLAUDE QUEUE SECOND COMPLETE` appeared with `queued · queue 1` and Cancel, then started
   automatically after `CLAUDE QUEUE FIRST COMPLETE`; both exact answers appeared once in order.
8. The first real Send Now probe found a provider-specific boundary defect rather than being counted
   as a pass: Claude completed a background `sleep 60` task after the successor and emitted the
   forbidden predecessor answer. The narrow correction makes Claude declare that requested
   cancellation requires the existing same-binding fresh-child recovery even after a normal terminal
   response. In the exact live retest, the active old turn owned a background `sleep 45` plus a
   foreground wait, Send Now interrupted it, old child PID `2477` was replaced by PID `7324`, and the
   unchanged session/binding remained
   `e2562875-0b3f-482e-8f75-52e0d0e46731` generation 1. The successor returned exactly
   `CLAUDE ISOLATION RETEST SUCCESSOR OK 20260721`; the forbidden old completion never appeared after
   the prior late-output interval. Provider-durable old task status replayed before the successor
   human boundary and did not enter or follow the successor.
9. Claude's native plan-mode path supplied the live permission interaction that ordinary `auto`-mode
   Bash intentionally does not. `EnterPlanMode` prepared a read-only README plan and `ExitPlanMode`
   displayed the real ACP permission card `Ready to code?` with five exact options, including
   `Yes, and use "auto" mode` and `No, keep planning`. Selecting the auto option settled the card,
   restored auto mode, and completed the read-only probe without changing repository files. The
   adapter's disposable plan file was moved to Trash afterward.

Verdict: **PASS** for real human -> naturally discovered Automatic Employee -> human -> successful
compaction/reload -> server restart continuity and the full required provider-control matrix through
one selected Claude backend/session.

## Durable corroboration after the final restart

Read-only queries against `data/planning.db` after both Safari probes returned:

| Ticket | backend | durable ACP session | generation | Employee run | run session | status |
|---|---|---|---:|---|---|---|
| `t_x2f5up6e` | `hermes` | `f5b57fd7-7873-4285-9f6c-0e6d0ee219d4` | 1 | `run_1wcjzguy` | same | complete |
| `t_nkq3108b` | `codex` | `019f81a8-7041-7bb3-b7da-4445912fc3d0` | 1 | `run_3k18evvz` | same | complete |
| `t_1xbdpkq0` | `claude` | `e2562875-0b3f-482e-8f75-52e0d0e46731` | 1 | `run_jsmxnrt8` | same | complete |

For all three rows, `tickets.employee_session_id`, the binding's `acp_session_id`, and the completed
Employee run's `employee_session_id` are byte-identical. The later server-restart replies did not
change the Codex or Claude binding generation.

The older Hermes Ticket `t_b7sdhtzn` is retained only as ACP-05 evidence. Its current binding was
intentionally reset by the later schema-25 one-way legacy cutover, so its current post-cutover session
must not be compared to the pre-cutover completed-run session as if no authorized reset occurred.

## Fixture-only distinctions

Automatic compaction admission, two-browser disconnect/ordering, held-gate deadline, general FIFO
races, permission first-settlement, and callback permutations use their named deterministic tests
because they cannot all be induced honestly and repeatably through a single UI session. The real app
proof above independently establishes the corresponding statuses and controls, including the exact
Claude late-background-output failure and passing fresh-child retest; deterministic tests establish
the remaining race invariants. No fixture result is presented as Computer Use.
