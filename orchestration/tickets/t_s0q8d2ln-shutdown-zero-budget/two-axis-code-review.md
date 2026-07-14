# Two-axis code review

Fixed point: `bf2ac9b5337c9668d880e0875baa3e2b97669fd9`
Working-tree diff: `git diff bf2ac9b` plus untracked ticket artifacts and process test
Commit list: empty (`git log bf2ac9b..HEAD --oneline`)

## Standards

Hard violations:

- `PROGRESS.md:11–44` is stale: it still says production is unchanged and implementation is next,
  contrary to the memory rule in `AGENTS.md` and `CLAUDE.md`.
- `docs/employee-runtime.md:113–119` and `docs/systems.md:229–236` duplicate the same shutdown
  account, contrary to `docs/CLAUDE.md`'s cross-cutting rule that duplication rots.
- Those documentation paragraphs use unexplained implementation vocabulary as substance, contrary
  to `docs/CLAUDE.md`'s plain-language rule.
- New tests repeatedly name a Ticket id `tid`, contrary to the repository's exact-name rule.

Judgment calls:

- Possible Primitive Obsession / Data Clump: `employee_step_runner.py` carries Employee session id,
  Ticket id, and turn id as an anonymous three-string tuple through several loops.
- Possible Duplicated Code: new local test gateways repeat the same dynamic available-status object.

## Spec

- Wrong: the deadline-aware interrupt computes remaining time before
  `LiveSessionManager._interrupt`, but `_interrupt` acquires `state.command_lock` without a timeout
  and then waits the original duration. The request is not bounded by the same deadline.
- Wrong: `SharedGateway.shutdown` converts the absolute deadline to one relative `grace`, while
  `GatewayChild.shutdown` spends that grace independently on process wait, post-kill wait, stdout
  join, and stderr join. Child cleanup can exceed the shared deadline.
- Wrong/scope creep: the unconditional late-complete gate also affects ordinary Pause. If the turn
  was interrupted outside shutdown and Hermes races back complete, the runner leaves the Ticket
  running instead of retaining ordinary interruption's errored behavior.
- Partial TDD evidence: the implementation report says missing-id and parked-reservation guards were
  already green on the baseline although the contract calls for RED before production change.

Summary: Standards reported four hard findings and two judgment calls; the worst hard issue is stale
memory, and the largest design smell is the anonymous identity tuple. Spec reported four findings;
the worst are the two paths that can outlive the one shutdown deadline.
