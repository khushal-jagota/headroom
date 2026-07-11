1. `decisions.md:5` and `decisions.md:1853` both define `D99`, making the ticket’s required decision reference ambiguous.

2. `web/src/routes/TicketRoute.svelte:300` enables recap editing for `needs_success` after `needs_kickoff` was prepended to `STATE_ORDER`, but `src/planner/tickets/logic/admission.py:73` still rejects recap writes in `needs_success`.

3. `docs/cli.md:27` still documents `ticket create --user-note` and `ticket set user-note`; `docs/cli.md:44` and `skills/panels-chief-of-staff/SKILL.md:64` still tell Chief intake to use `--user-note-file`, contradicting the canonical `kickoff_note`/`--kickoff-note-file` contract.

4. `docs/tickets-and-gates.md:17` and `docs/tickets-and-gates.md:37` still say direct resolution can jump a ticket anywhere, but `src/planner/tickets/logic/resolution.py:275` correctly forbids direct transitions from or to `needs_kickoff`.

5. `tests/unit/test_db.py:126` only covers Kickoff migration happy path, while `tests/unit/test_db.py:378` covers lifecycle rollback before Kickoff migration; backend-review’s required Kickoff-only rollback regression remains missing.
