# ACP-02 plan review — correction check

## Verdict

**NOT READY**

Six of the eight round-1 findings are resolved. Findings 3 and 6 remain incomplete at two concrete
ACP-01/Hermes integration boundaries. This check is limited to the original findings and the requested
generation-replacement, compaction, permission, confinement, and terminal-deadline corrections.

## Finding-by-finding disposition

### 1. Cancel-settlement timeout and exact retirement — RESOLVED

The amended plan arms the cancel-settlement timeout before permission or transport work, owns the
cancel-send and permission-cancel tasks, and cancels/awaits them if the timeout wins
(`plan.md:267-286`). Both Send Now and active browser cancel use this sequence. Exact-generation
retirement is awaited within the active shared deadline rather than scheduled as fire-and-forget, and
the acceptance map now holds the cancel send itself while proving the reject-all publication order
(`plan.md:691`, `plan.md:706`).

### 2. New-conversation and shutdown successor policy — RESOLVED

The cancellation-cause table freezes `no_successor` before transport work. New conversation and
shutdown interrupt the active prompt, reject the whole FIFO, publish the empty snapshot, and start
nothing (`plan.md:242-254`). The acceptance map includes response-versus-new and
response-versus-shutdown races proving no successor invocation (`plan.md:691`, `plan.md:707`).

### 3. Exact-generation runtime adapter and native Steer — UNRESOLVED

The amended runtime design does resolve most of this finding. It introduces a full-identity handle and
lease, puts the atomic methods inside `AcpEmployeeRegistry`, captures the exact child under the
employee gate, releases the gate before `child.prompt`, and makes stale exact-child operations fail
instead of redirecting to N+1 (`plan.md:63-96`). Capture explicitly quiesces and retires N before
spawning/private-loading/publishing N+1, and preserves the durable binding (`plan.md:74-89`,
`plan.md:159-174`). The paused-Steer acceptance test also proves that an operation for N cannot reach
N+1 (`plan.md:694`, `plan.md:709`). These satisfy the requested gate, no-redirect, ordering, and
unchanged-binding invariants in the normal path.

The replacement path still omits one required ACP-01 lifecycle mechanic. Current
`AcpEmployeeRegistry.guarded_death` invokes `conversation_child_death_callback` after every child
close, including intentional close (`src/planner/conversation/employee_registry.py:337-348`). During
capture, the plan retires N before N+1 exists (`plan.md:159-164`). It never marks that exact N death as
planned or says how the actor distinguishes it from the unexpected-current-generation death path,
which fails every open boundary and the actor (`plan.md:312-320`). The only late-retirement no-op rule
is for cancel-timeout retirement (`plan.md:280-286`), not successful compaction replacement. Therefore
N's expected close callback can tear down the capture-finalizing actor before N+1 is privately loaded
and published.

Amend the plan with an exact record/generation-scoped planned-retirement disposition installed before
closing N and consumed by the death callback/turn actor, without suppressing a real unexpected death.
Add a latch test that delivers N's close callback before N+1 spawn/load/publication and proves the
boundaries remain open for capture, N+1 is published once, and no child-failure cleanup runs. Also
reconcile the claimed “one-line delegation”/“no registry lifecycle ... changed” scope
(`plan.md:39-42`) with the multiple registry lifecycle operations the amended plan now requires.

### 4. Source-aware permission admission and atomic late-open handling — RESOLVED

Each spawn now receives a one-argument callback closure carrying the employee, complete binding/record
identity, and child generation. The SDK bridge shield-awaits the broker future, while the turn actor
serializes admission against cancellation/death/new/shutdown and tombstones the epoch/generation
before settling it (`plan.md:442-452`). Admission validates all source tokens and the capability, and a
late callback receives exact cancelled without publication (`plan.md:460-473`). Both `deadline_at` and
the timeout task use the injected timeout. The acceptance map covers same-session/different-employee,
late-open, callback-task cancellation, and injected timeout cases (`plan.md:695`).

### 5. Multiple distinct automatic compactions and explicit/provenance dedupe — RESOLVED

The actor now owns an ordered boundary map. Only the same provenance observation identity is
deduplicated; each later distinct transition opens another automatic boundary (`plan.md:364-373`). For
an exact `/compact`, the first provenance transition attaches to the already-open explicit boundary,
its duplicates are suppressed, and later distinct transitions open automatic boundaries
(`plan.md:375-380`). One post-prompt capture finalizes every boundary in observation order
(`plan.md:382-393`). The acceptance map exercises two distinct transitions plus their duplicates and
the explicit/provenance association (`plan.md:693`).

### 6. Private-capture isolation and implementable summary extraction — UNRESOLVED

The live-update isolation half is resolved in design. A fresh unpublished N+1 has a private ingress,
old N is quiesced/retired before N+1 is spawned, only N+1's load can populate the capture window, and
the ingress switches to ordinary delivery before publication (`plan.md:142-174`). No update gate is
held across a prompt; delayed N traffic either reaches N's ordinary sink before transition or fails
the stale-generation check. Capture failure closes unpublished N+1 and never rebroadcasts its prefix.
This remains subject to the planned-retirement callback gap in finding 3.

The summary extractor is not yet compatible with pinned Hermes. The plan scans only official user
text chunks and expressly excludes agent text (`plan.md:422-430`). Pinned Hermes chooses a standalone
compressed summary's role dynamically: it can be either `user` or `assistant`, and may merge the
summary into the first tail message when neither standalone role preserves alternation
(`/Users/khushaljagota/.hermes/hermes-agent/agent/context_compressor.py:3063-3097`). ACP replay turns an
assistant-role summary into an agent text chunk. The proposed predicate will therefore report a
visible capture failure for valid Hermes compacted histories.

Amend the extractor to cover every role/merged placement actually emitted by pinned Hermes while
retaining the stated exact-prefix, typed-boundary, exact-one-candidate, and ambiguity-failure rules; do
not restore a provenance guarantee that ACP replay cannot carry. Add focused fixtures for standalone
user-role, standalone assistant-role, user-tail merged, and assistant-tail merged summaries, plus the
zero/multiple-candidate failures already planned.

### 7. Descriptor-backed safe-symlink confinement — RESOLVED

The plan now distinguishes a dangling final symlink from a missing target with no-follow metadata,
canonicalizes permitted internal symlinks, traverses from opened root descriptors with
`O_NOFOLLOW`, opens the canonical final object via `dir_fd`, and validates the opened descriptor with
`fstat` (`plan.md:509-528`). Reads and writes use only that descriptor. The acceptance map adds the
dangling-final-symlink and deterministic parent/final path-swap cases for both operations
(`plan.md:696`, `plan.md:716`).

### 8. Force-kill semantics and end-to-end terminal deadlines — RESOLVED

`terminal/kill` now uses `Process.kill()` and reports success only after the common watcher has
observed exit, drained/flushed decoding, and published the exited snapshot; failure to establish exit
ownership before the absolute deadline is typed (`plan.md:591-600`). Release uses one absolute
deadline across graceful wait, force kill, process wait, decoder flush, exit/released publication, and
reader/watcher/publisher cancellation and await (`plan.md:602-618`). Deadline expiry force-kills,
removes the live handle, and reports the terminal ID unfinished rather than hanging. The acceptance
map includes a SIGTERM-ignoring process and latches on decoder drain and final publication
(`plan.md:697`, `plan.md:712-718`).

## Required corrections before READY

1. Define and test exact-generation planned-retirement handling so N's intentional close during
   capture cannot enter unexpected-child-death cleanup before N+1 publication; reconcile the file-scope
   statement with the registry lifecycle work.
2. Make Hermes summary extraction accept every valid pinned user/assistant and merged replay placement,
   with exact-one-candidate ambiguity failure and without claiming unavailable `_compressed_summary`
   provenance.
