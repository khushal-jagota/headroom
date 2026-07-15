# Two-axis implementation review

Fixed point: `main`

Diff: `git diff main...HEAD`

Commits:

- `7c3508f feat: supervise Panels restarts`
- `430cf92 docs: keep workers from restarting Panels directly`

## Standards

Hard violations:

- `docs/cli.md`, `docs/employee-runtime.md`, and `docs/systems.md` repeat the same
  source-root capture, supervisor replacement, no-PID-signalling, and failure behavior.
  This violates `docs/CLAUDE.md`: facts belong in one best location and duplication must
  be removed. `systems.md` should own the lifecycle shape; `cli.md` should state only
  command behavior; `employee-runtime.md` should contain a short handoff.
- Those same hunks use implementation vocabulary—“port-scoped lifecycle lease,”
  “application child,” “versioned request,” “control socket,” and “source root”—as the
  explanatory substance. This violates the simple, plain-language documentation rules
  in `AGENTS.md` and `docs/CLAUDE.md`.

Judgment calls / baseline smells:

- **Duplicated Code:** `control.py` and `supervisor.py` independently implement bounded
  newline-framed socket reads, including the same EOF/size control flow. A shared framing
  helper would give the protocol one implementation.
- **Mysterious Name:** `_CONTROL_READ_BYTES` is named as a read size but is also used as
  the maximum request length, hiding its contract role.

No other documented-standard violations or named baseline smells were found.

## Spec

Two requirements are implemented incorrectly:

1. **Operator shutdown can be blocked before request acceptance.** Contract `panels
   serve`: “On operator SIGINT or SIGTERM, gracefully terminate the current child…”
   `supervisor.py` switches a new socket to blocking mode and waits indefinitely for a
   newline without observing the signal pipe. Reproduction: connect without sending;
   SIGTERM does not exit until the client closes.
2. **A child crash concurrent with a queued restart becomes an unauthorized crash
   retry.** Contract `panels serve`: “An unexpected application-child exit ends the
   supervisor with a non-zero result. This ticket adds no crash-retry daemon.” A ready
   SIGCHLD is drained, but control requests may be accepted before child state is
   rechecked; restart then takes precedence. Reproduction: stop the supervisor, queue a
   restart, kill its child, and resume; it accepts and spawns a replacement.

No other missing requirement or scope creep was found. The client is socket-only; the
lease precedes socket removal; accepted-response/client-close ordering, captured
launch state, one-child serialization, and both skill rules match the contract.

Summary: Standards found two hard documentation violations and two code-smell judgment
calls; the worst standards issue is duplicated, implementation-heavy live documentation.
Spec found two lifecycle races; the worst spec issue is turning an unexpected child exit
into an unauthorized restart.

## Disposition

Every finding is accepted.

- `docs/systems.md` now owns the lifecycle shape in plain language. `docs/cli.md` keeps
  only caller-visible command behavior, and `docs/employee-runtime.md` contains a short
  recovery handoff.
- One `receive_server_control_message` implementation now owns bounded newline framing.
  The accepted-client drain has its own descriptive buffer name.
- An incomplete request now observes the signal wakeup path, so operator shutdown and
  unexpected child exit can supersede it.
- The outer lifecycle loop checks operator and child state before accepting a ready
  control request. A queued request can no longer turn an already-dead child into a
  replacement generation.
- Both lifecycle races have RED-first real-process regressions.

## Closing re-reviews

The first corrected re-review returned `NO FINDINGS` on the Spec axis. Standards found
two remaining cleanup items: developer vocabulary in `docs/systems.md` and duplicated
signal-aware selector loops inside the supervisor. Both were accepted. The system doc
now uses plain user language, and one lifecycle-aware receive function owns both the
incomplete-request and accepted-client waits.

Final results:

- Standards: `NO FINDINGS`
- Spec: `NO FINDINGS`
