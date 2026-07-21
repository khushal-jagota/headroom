# ACP-05 Send Now post-recovery human-boundary implementation report

## Outcome

Requested-cancel recovery now restores every human boundary erased by its same-binding browser reset.
The hub publishes the retained FIFO prompts in their original order, then the optional Send Now
successor, then the FIFO-only queue snapshot. The broker starts the captured successor only after that
atomic recovery commit returns.

The successor is carried through the existing typed `HumanEcho` payload with its exact
`client_message_id` and `PromptRequest`. No wire event, browser heuristic, reducer rule, visual code,
durable binding field, or backend-specific path was added.

## Proof

- The initial focused regression failed because the recovery commit accepted no successor human echo.
- Hub coverage proves exact `reset -> missing-ID replay -> ready -> FIFO human echo -> successor human
  echo -> FIFO-only queue snapshot` publication to both attached browsers.
- Broker coverage proves the retargeted frozen successor contributes one exact typed echo and cannot
  start on the fresh child until recovery commit completes.
- The official-SDK subprocess recovery proves the same order, one post-reset echo per retained prompt,
  one successor start, one successor answer, no late old-generation output, unchanged durable binding,
  and fresh child identity.
- The production browser reducer consumes the complete recovery-shaped stream and projects separate
  old agent, retained FIFO user, successor user, and successor agent messages. The old and new agent
  text never concatenate.

## Focused gate

Ruff and strict Mypy pass. All 74 named hub, broker, and official-SDK e2e tests pass. The four ACP
browser suites pass, and Svelte diagnostics report zero errors and zero warnings. Canonical `./verify`
was not run; ACP-10 owns it.

## Scope

Only the ticket's allowed port, broker, hub, focused Python/browser tests, evidence, and progress record
changed. Hermes, the public wire schema, frontend source/reducer/component, generated `web/dist`, and
the durable binding schema were untouched.
