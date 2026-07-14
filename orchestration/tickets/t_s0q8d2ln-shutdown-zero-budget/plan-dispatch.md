# Planning dispatch — t_s0q8d2ln

Write `implementation-plan.md` for the contract in this directory. Do not edit production code,
tests, contracts, docs, `PROGRESS.md`, or `decisions.md`.

The plan must:

1. inspect the existing restart-recovery contract and tracer bullet 5;
2. identify one vertical RED → GREEN slice for each accepted public seam;
3. explain how active Employee session ids are snapshotted and interrupted without holding the
   runner condition lock across gateway or SQLite work;
4. preserve one absolute deadline without adding a config value or resetting the clock;
5. distinguish an expired, inconclusive zero-time router observation from a genuinely stuck router
   that remains alive after a positive wait;
6. ensure every unique routed role gateway is attempted before the first error is re-raised;
7. name every file and exact focused test command; and
8. keep all changes inside the ticket's file-ownership list.

Record ambiguities as delegated choices in the plan. Do not ask the owner questions.
