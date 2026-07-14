# Implementation review disposition

## First complete-diff review

1. **Chief reconciliation forced Stop.** Accepted. `decide_external_work` now changes only the ceiling and carries the Ticket's existing `at_cap` in the scope event. Focused tests prove default `propose`/Continue and explicit Stop are both preserved.
2. **Future-stage ownership event reported the current Stage's owner.** Accepted. The event now resolves effective ownership for the Stage named in the event. A focused future-stage event test pins the payload.
3. **Worker-type read-path documentation was stale.** Accepted. The docs now explain that Ticket reads resolve the Worker-type definition to derive default/effective ownership.

## First re-review

1. **v21 migration evidence did not cover every legacy implementer mapping or active statuses.** Accepted. A focused v18 -> v20 -> v21 migration test now covers `khushal` for coding/non-coding, every agent route, and preservation of `agent_running_step`, `awaiting_approval`, `user_takeover`, `errored`, and `empty`.
2. **Automatic eligibility evidence did not isolate all three ownership modes or include `paired_work`.** Accepted. A focused parameterized test now holds every other conjunct constant for Worker/User/Paired, and `paired_work` is included in the non-empty control-status table.
3. **Paired ordinary Chat had no same-session/no-proposal evidence.** Accepted. A focused `ChatTurnLifecycle` test now starts ordinary Ticket Chat with a durable Employee session, proves the gateway receives that session, and proves completion without a proposal leaves the Ticket in `paired_work`.

No finding was refuted. A final read-only re-review follows these corrections before canonical verification.
