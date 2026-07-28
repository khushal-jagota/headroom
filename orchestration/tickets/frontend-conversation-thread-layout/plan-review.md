# Plan review: Frontend conversation thread layout

## Standards

**PASS.** The plan places the non-trivial pure layout module in a semantic folder,
keeps its interface to the locked type and one function, tests through that interface,
and preserves a type-only, acyclic dependency on the existing contracts. All
dependencies are in-process, so avoiding a port or adapter is correct.

## Spec

**FINDINGS — resolve the two concrete plan gaps before implementation.**

### [Spec-1] Replace the tests' local `ThreadItem` aliases when importing the locked type

The RED steps say to import `ThreadItem` from `threadLayout`, but
`conversation-thread-items.test.ts:26` and `conversation-thread-plan.test.ts:16`
already declare a local `ThreadItem` with `ReturnType<typeof threadItems>[number]`.
Following the plan literally produces duplicate type declarations, so the first RED
is not solely the promised missing-module failure and the suites cannot become GREEN
after the module is added. `conversation-turn-time.test.ts:24-27` likewise continues
to derive its turn type from `ReturnType` despite the plan saying to import the locked
type.

Amend steps 1 and 3 to replace the two local `ThreadItem` aliases with type-only
imports from `threadLayout`, and to define the turn-time `TurnItem` as
`Extract<ThreadItem, { kind: "turn" }>` from that same imported contract. This keeps
the locked interface as the test surface without changing any behavioral assertion.

### [Spec-2] Make the export audit capable of detecting forbidden exports

The audit at `implementation-plan.md:264-271` searches only for the two expected
declarations:

```sh
rg -n "^(export type ThreadItem|export function threadItems)" ...
```

That command still reports the expected two lines when the module also exposes a
forbidden helper, constant, or alias, so it cannot prove the contract's “no other
exports” rule. Replace it with an audit that lists every export, such as
`rg -n "^export\\b" web/src/lib/conversation/threadLayout/index.ts`, and require the
complete result to be exactly the locked type and function.

There are no other unresolved Standards or Spec findings.
