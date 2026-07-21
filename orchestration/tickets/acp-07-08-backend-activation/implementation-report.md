# ACP-07/08 shared backend activation — implementation report

## Outcome

The shared production activation is complete. The sole production Employee-backend catalog is now
ordered `hermes`, `codex`, `claude`; the production Worker registry shares that exact catalog
instance, while Chief and every shipped Worker-type default remain Hermes.

The common materialization context now carries the resolved absolute Panels repository root. A
materialized backend may expose one async startup preflight. Composition retains those hooks in
catalog order and awaits them serially at most once without asking the Employee registry to create
an ordinary child.

Production startup now completes storage audit, composition, and provider preflights before it
publishes the conversation service or starts Employee runtime loops. A preflight failure closes
admission, shuts down the composition under the existing single deadline, leaves the application
conversation unset, starts no runtime, and re-raises.

The settled provider builders are imported without startup probing or spawning. Hermes and Codex
have no startup preflight. Claude alone supplies the bounded initialize-only preflight. Aggregate
availability remains a live read of the materialized providers' executable probes; no fallback or
provider-specific routing was added.

## Implementation notes

- The initial shared acceptance checks were red while the production catalog was Hermes-only.
- The first combined final gate found one remaining test expectation whose error detail still named
  only Hermes. The runtime correctly returned all three registered backends; the stale assertion was
  updated and the unchanged combined gate then passed.
- No provider module, provider test, Ticket/Worker schema, UI, runtime port, package file, docs file,
  or Hermes checkout was edited by this shared slice.
- `./verify` and live dogfood were deliberately not run; ACP-10 owns the frozen-tree verifier and
  dogfood gate.

## Result

Focused shared acceptance is 37/37. The integrated Codex, Claude, and generic in-place compaction
set is 51/51. Ruff, strict Mypy, and the scoped whole-file diff check pass. The integrated diff is
ready for its one focused independent review.
