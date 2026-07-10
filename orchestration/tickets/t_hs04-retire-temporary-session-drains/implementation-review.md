# t_hs04 implementation review resolution

The final independent reviews found seven actionable gaps and one questioned lock scope.

1. The AST boundary inspected banned calls but not banned definitions. It now rejects definitions of
   `open_session_events`, `_submit_and_drain`, and `_stream_prompt` as well as call sites.
2. `LiveSessionManager.shutdown` silently ignored a router still alive after its bounded join. A RED
   test now proves the failure is reported. Shutdown records and replays that bounded error to
   concurrent callers, while `SharedGateway.shutdown` always reaps the child in `finally`. Normal
   shutdown coverage asserts the router is dead.
3. The rewritten concurrency smoke lacked an executable cross-session rejection contract. A new test
   drives its single claimed feed and proves an unexpected session fails without another listener or
   queue.
4. Employee-runtime documentation implied queued acceptance acknowledged pending context. It now says
   streaming and steered delivery acknowledge immediately, while queued employee context waits for
   the owned execution to start; unknown and pre-start failure retain it.
5. The implementation report summarized rather than retaining the complete captured `./verify`
   output. The original captured output is now included verbatim. It was not rerun merely for quoting.
6. Gateway and SharedGateway docstrings still described deleted per-session drains and a worker-only
   owner. Both now describe the child-wide ingress and one role-configured child.
7. A deterministic RED test proved that a streaming lifecycle arriving before its receipt could be
   discarded when the resume snapshot said `running=true`. The router now distinguishes observations
   buffered behind a registered Panels predecessor from a gapless streaming lifecycle buffered only
   behind a snapshot/unknown barrier. The latter is delivered to the streaming consequence; the
   former still waits past the predecessor. Existing Stop-then-send, command overlap, queued, and
   steered tests remain green.

The image prompt-admission lock finding is refuted. The narrow hold is required between native
`image.attach` and a known prompt receipt so a later prompt cannot inherit the rejected image.
`test_ready_rpc_error_is_removed_before_later_lifecycle_routing` proves a later prompt cannot write
until exactly one detach attempt completes, while `session.interrupt` remains available during the
hold. `test_unknown_human_submit_retains_context_without_retry_or_image_detach` proves an unknown
delivery is neither detached nor retried. The hold does not wait for the model lifecycle and does not
hold the broader command lane.

Final focused result after these resolutions: 157 tests passed; Ruff passed; Mypy passed across 104
source files; `git diff --check` passed. Both independent follow-ups returned `NO VIOLATIONS`. The
parent's fresh post-review integrated `./verify` then passed with 394 unit tests, 57 browser tests,
and `VERIFY: PASS`.
