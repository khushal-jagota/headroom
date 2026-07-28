# Plan review disposition

## Spec-1: replace local test aliases

Accepted. Steps 1 and 3 now explicitly remove the two local `ThreadItem` aliases and
derive the turn-time suite's `TurnItem` from the imported locked type. The RED result can
therefore be the missing module alone, and the exported type is the interface under test.

## Spec-2: audit every export

Accepted. The export audit now lists every line beginning with `export` and requires the
complete result to be exactly the locked type and function. An additional helper,
constant, alias, or compatibility export can no longer hide behind a positive-only
search.
