# ACP-02 plan review — round 1

## Verdict

**NOT READY**

The plan has the right domain boundaries and covers most of the contract in unusually concrete
detail. The blockers below are narrower: they are places where the proposed ordering cannot yet
guarantee the frozen behavior against the integrated ACP-01 lifecycle and the pinned SDK. No route,
database, frontend, or legacy-cutover scope is needed to resolve them.

## Findings

### [P1] 1. The cancel-settlement timeout starts after an await that can itself hang

The actor is allowed to await the one-way ACP cancel (`plan.md:163-168`), and Send Now says to send
cancel and only then arm the timeout (`plan.md:219-225`). The integrated child implementation awaits
the SDK connection send in `SdkAcpEmployeeChild.cancel` (`sdk_child.py:309-314`). If that send stalls,
the actor cannot process death or shutdown commands and the named cancel timeout never exists. The
Send Now submission and FIFO therefore need not reach the required reject-all disposition.

The timeout must start when the active epoch enters cancelling, before any transport await. The cancel
send must be an owned, generation/epoch-tagged task raced with prompt settlement, timeout, and child
death. Timeout must cancel/await that task and own exact-generation retirement through completion or
the shared shutdown deadline; merely “schedule” retirement (`plan.md:231-235`) can leave an
indeterminate child running after the broker has reported failure. Add a latch test where the cancel
send itself is held, plus the existing response/timeout/death race, and assert the exact interrupted,
Send Now rejected, FIFO rejected, empty snapshot, failed order once.

### [P1] 2. New-conversation and shutdown cancellation have no frozen settlement branch

The common active-cancel branch advances and starts one queued prompt after settlement
(`plan.md:247-251`). `prepare_new_conversation` then says it cancels and settles the active prompt and
rejects the FIFO (`plan.md:274-277`), while shutdown uses similarly broad prose (`plan.md:279-284`).
There is no stated rule preventing the common completion handler from popping and invoking the queue
head before the closing operation rejects what remains.

Freeze a cancellation-cause disposition table in the actor: user cancel may advance one FIFO item;
Send Now starts only its submitted prompt; new conversation and shutdown interrupt the active item,
reject the entire FIFO in order, publish the empty snapshot, and never create a successor task; child
failure follows its already-written reject-all branch. Each closing cause must set its no-successor
state before sending cancel. Add response-versus-new and response-versus-shutdown latch tests proving
that no queued child prompt is invoked. This is the missing exact active-turn/FIFO mechanic; the
ordinary receipt and queue ordering elsewhere in section 3 is adequate.

### [P1] 3. The ACP-01 runtime adapter and native Steer path are not exact-child-generation scoped

`ConversationEmployeeRuntimePort` is described declaratively as resolving, retiring, and privately
loading an exact handle (`plan.md:63-75`), but the integrated `AcpEmployeeRegistry` exposes none of
those operations. Its record table and employee publication gate are private. Implementing the port
outside the registry cannot atomically check both generations, retire only the current record, or
preserve ACP-01's rule that generation N callbacks quiesce before N+1 publication. This is more than
the optional “one-line delegation” allowed at `plan.md:39-42`; the plan needs the lock/gate algorithm
and the exact new internal registry methods before implementation can be typed and race-safe.

The same gap is visible in Steer. The proposed concurrent prompt port accepts only a binding and a
`PromptRequest` (`plan.md:77-84`), and the frozen `BackendTurnStrategy.steer` also receives no child
generation. A binding survives a crash. If strategy execution pauses across death/respawn, resolving
“current by binding” can send `/steer ...` to generation N+1 even though its active prompt was on N.
Pinned Hermes rewrites an idle `/steer` into an ordinary prompt
(`~/.hermes/hermes-agent/acp_adapter/server.py:1323-1354`), turning this race into real stale worker
delivery rather than a harmless rejection.

Specify a generation-bound runtime lease/operation captured before strategy scheduling, and have the
registry reject it unless employee, binding generation, child generation, record identity, and
aliveness still match. Controlled capture must use the same gate and must not bypass the generation
wrapper merely because its sink is private. Tests must pause Steer and capture before child death,
publish N+1 with the same durable binding, and prove that neither operation reaches N+1.

### [P1] 4. Permission callbacks lack source identity and an atomic late-open rule

Section 5 assumes that a permission open knows employee, binding generation, child generation, and
active prompt epoch (`plan.md:363-385`). The actual ACP-01 callback type carries only the exact
`RequestPermissionRequest` (`backend_contracts.py:125-127`); the registry passes one shared callback
unchanged to every child (`employee_registry.py:332-337`), and the SDK bridge adds no employee or
generation context (`sdk_child.py:171-187`). Session ID alone is not the frozen ownership key and
cannot safely identify a child generation.

There is also no atomic rule for this race: active cancel enqueues “cancel permissions for epoch N,”
then a reverse permission callback from N arrives and opens after that command observed no pending
request. Such a request can remain visible until timeout even though active cancel was required to
cancel it. The same issue applies to death, binding replacement, new conversation, and shutdown.
Finally, the callback awaits the broker future directly (`plan.md:381-385`); cancellation of the
SDK request-handler task on child death will cancel an unshielded future before the death settlement
owner runs.

Have the child/factory path wrap the broker call with its captured employee and child generation.
Permission admission must atomically consult a broker-owned active-epoch lease or a closed-epoch /
closed-generation tombstone, so an open after any terminal cause returns ACP `cancelled` without
publishing. The broker settlement future must be shielded from caller-task cancellation; only the
permission actor may settle it. Also compute both the published `deadline_at` and timeout schedule
from the injected timeout value: `integer_now + 300` at `plan.md:381` contradicts the injected timeout
declared at `plan.md:363-367` and the sole configuration default. Add same-session-ID/different-
employee, late-open-after-cancel, callback-task-cancelled-on-death, and injected-timeout tests.

### [P1] 5. Distinct automatic compactions in one prompt are deliberately dropped

The contract requires one automatic compacting boundary per distinct provenance transition and only
deduplicates duplicate provenance (`contract.md`, “Compaction normalizer”). The plan gives an actor
only one optional pending boundary (`plan.md:151-161`) and explicitly suppresses a second observation
while the epoch owns one (`plan.md:307-315`). A long prompt can compress more than once, so a real
second transition becomes silent.

Represent pending boundaries by distinct observation identity rather than a single optional value.
Freeze how explicit-command provenance is recognized as the same transition versus a genuinely new
automatic transition, and finalize/fail every opened boundary exactly once after the owning prompt
settles. Add one prompt fixture with two distinct `(previous, current, depth)` transitions plus a
duplicate of each; it must publish two boundaries and no duplicates.

### [P1] 6. Private capture has no isolation from unrelated live updates, and its claimed summary provenance is unavailable

The proposed ingress marks every slot between an outgoing capture load and its response as private
(`plan.md:120-136`). ACP `session/update` has no request/capture correlation. “No running prompt” does
not prove that a delayed post-response notification, scheduled usage/title update, or other live
session notification cannot arrive during that interval. Such an update would be swallowed by the
collector instead of reaching ordinary ingress, violating both capture isolation and ordered live
delivery. The current tests cover a failing replay prefix but not an unrelated live notification
crossing the capture window (`plan.md:600-609`).

The summary predicate also promises not to treat an arbitrary user string as a summary
(`plan.md:353-359`). In pinned Hermes, `_compressed_summary` is internal message metadata, but ACP
replay reconstructs only a `UserMessageChunk` or `AgentMessageChunk` containing text
(`~/.hermes/hermes-agent/acp_adapter/server.py:972-990,1023-1073`). That provenance flag does not reach
the SDK notification. A user message containing the exact prefix/end marker is therefore
indistinguishable by the proposed typed capture.

The plan must name an implementable quiescence/barrier that prevents non-capture traffic from being
redirected, without a quiet-period heuristic, and test a deliberately delayed ordinary update during
capture. For summary extraction, either state a stricter structural/positional rule actually supported
by the pinned replay and fail ambiguous captures, or remove the impossible “arbitrary user string”
claim; do not claim provenance that Hermes does not emit. Capture failures must still use the same
broker boundary and must never leak private slots to the ordinary sink.

### [P1] 7. New-write confinement does not implement its own dangling-symlink rule

The plan says a dangling symlink is rejected, but its algorithm treats a non-existing target as a new
file, resolves only the immediate parent, appends the final filename, and writes it
(`plan.md:421-443`). `Path.exists()` is false for a dangling symlink. Opening that appended path can
follow the final symlink outside every workspace root. The same resolve-then-open split permits an
existing final component to be replaced by an escaping symlink between validation and I/O.

Use `lstat`/no-follow semantics for the final component and open relative to a verified directory
descriptor (or an equivalent descriptor-backed containment check) so the object actually opened is
the object confined. At minimum, a dangling final symlink must be distinguished from a missing path
before the new-file branch. Add deterministic dangling-final-symlink and path-swap tests for both read
and write; the existing traversal/sibling/symlink cases do not prove this race.

### [P1] 8. `terminal/kill` is not guaranteed to kill, and cleanup is not bounded end to end

The contract says terminal kill terminates the process without releasing the ID. The plan sends
`Process.terminate()` once and returns (`plan.md:494-500`). On POSIX that is SIGTERM and can be
ignored, so the process may remain live after a successful `terminal/kill` response. Use force-kill,
or a named bounded terminate-then-kill policy that does not report success until exit ownership has
been established. Add a subprocess fixture that ignores SIGTERM.

The release algorithm budgets the graceful terminate wait, then separately waits/drains and publishes
exit/released state (`plan.md:502-506`). Those later awaits are not explicitly covered by the same
absolute deadline. Child death, new conversation, and shutdown all depend on this path, so a held
reader/wait/publication task can still exceed the promised bound. State that the one absolute cleanup
deadline covers graceful wait, force kill, process wait, decoder drain, exit publication, released
publication, and cancellation/await of reader/watcher/publisher tasks. Deadline expiry must force-kill
the process and return/report unfinished IDs without leaving tasks or handles. Extend the existing
shutdown latches to hold final terminal publication and decoder drain, not only process exit/release
entry.

## Confirmed areas

- The ordinary accepted/queued/started receipt order, one-based FIFO snapshots, head removal, queue
  single-delivery guard, and Send Now priority over the existing FIFO are otherwise concrete.
- Current-generation child death explicitly interrupts the active prompt, rejects a pending Send Now
  and the entire FIFO in order, publishes an empty snapshot, fails compaction, and does not auto-resume.
- Hermes-specific `/steer` construction and provenance parsing are confined to the strategy module;
  generic modules are forbidden from backend-name branches.
- The plan explicitly delivers worker input through live ACP `prompt`, never through a Chat/event/DB
  row, and retains ACP responses as settlement rather than transcript content.
- Exact SDK reverse models, argv-not-shell spawn, injected base environment, bounded UTF-8-safe
  terminal snapshots/replay, capability gating, typed publication, and the deterministic test ledger
  are directionally correct once the ownership and deadline findings above are repaired.

