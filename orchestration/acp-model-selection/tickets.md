# Tickets: Ticket Kickoff employee configuration

These tickets add Worker, Model, and Reasoning setup inside Ticket Kickoff, seeded once
from each Worker type and applied before the first ACP work prompt. The source research
is `research.md` and the settled generic contract is `contract.md`.

Work the frontier: the generic Ticket slice first, then the three production backends
may proceed in parallel.

## MR-01 — Build the generic Ticket employee-configuration slice

**What to build:** A new Ticket begins with its Worker type's configured Worker, Model,
and Reasoning values. Inside Kickoff, the user can independently edit the supported
values before approval. The saved configuration survives reload, freezes when work
begins, and a scripted ACP backend receives it before the first prompt.

**Blocked by:** None — can start immediately.

- [x] Worker-type defaults seed a Ticket once and never act as a reset target.
- [x] Worker, Model, and Reasoning are stored as one canonical Ticket configuration.
- [x] One atomic pristine-Kickoff writer prevents invalid intermediate combinations
      and races safely with the first durable session binding.
- [x] A read-only catalog resource discovers options without creating a Ticket binding.
- [x] The separate controls live inside the Kickoff section, never in the header or
      conversation chrome; unsupported Reasoning is omitted.
- [x] The first session is configured model-first, reasoning-second, before initial
      binding publication and before any prompt; bound-session loads do not reapply
      the historical Kickoff request.
- [x] Unknown or disappeared selections fail visibly without a silent fallback.
- [x] Migration, domain, concurrency, browser, and scripted first-prompt proofs pass.

## MR-02 — Enable Hermes model selection

**What to build:** A Hermes Ticket's Kickoff setup lists the current configured
provider's Hermes models, applies the selected model to its real ACP session, and never
shows a Reasoning control.

**Blocked by:** MR-01 — Build the generic Ticket employee-configuration slice.

- [x] Model discovery is read-only and does not modify or fork the Hermes checkout.
- [x] Hermes model application uses its existing ACP extension before first work.
- [x] Non-null Hermes reasoning is rejected at every boundary.
- [ ] Real Hermes dogfood proves the selected model is active on the first prompt.

## MR-03 — Enable Codex model and reasoning selection

**What to build:** A Codex Ticket's Kickoff setup lists the models and model-dependent
reasoning levels available to the current Codex account, then applies the chosen pair
before first work.

**Blocked by:** MR-01 — Build the generic Ticket employee-configuration slice.

- [x] Discovery uses the pinned Codex adapter's stable ACP config options.
- [x] Model is applied before reasoning and refreshed reasoning options are validated.
- [x] Temporary discovery sessions are closed and results are cached.
- [ ] Real Codex dogfood proves the selected model and reasoning on the first prompt.

## MR-04 — Enable Claude model and reasoning selection

**What to build:** A Claude Code Ticket's Kickoff setup lists the models and conditional
effort levels available to the current Claude configuration, then applies the chosen
pair before first work.

**Blocked by:** MR-01 — Build the generic Ticket employee-configuration slice.

- [x] Discovery uses the pinned Claude adapter's stable ACP config options.
- [x] Models without effort support omit Reasoning.
- [x] Temporary discovery sessions are closed and results are cached.
- [ ] Real Claude dogfood proves the selected model and effort on the first prompt.

## Integration and completion

**What to build:** Integrate MR-02 through MR-04 serially over MR-01, reconcile docs,
run one final independent review, execute the canonical verification gate once on the
settled tree, and dogfood fresh Tickets for all three production backends.

**Blocked by:** MR-02, MR-03, and MR-04.

- [x] No provider inventory is hard-coded into the frontend.
- [x] Existing bound Tickets retain their ACP session behavior; migrated null launch
      values are never applied to those sessions.
- [x] `./verify` passes once on the final settled tree.
- [ ] Safari dogfood confirms the restrained Kickoff placement and all real backends.
