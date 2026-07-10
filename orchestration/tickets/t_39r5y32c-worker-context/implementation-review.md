# Implementation review

Codex reviewed the isolated implementation diff in read-only mode with `gpt-5.5` and high reasoning effort.

## First pass

**High:** sync chat sends and sync model-backed commands could expose the augmented Hermes prompt through the product-visible history fallback because those legacy paths did not persist their original visible text.

## Resolution

The integration repair now:

- persists the original human text and reply for legacy sync send, legacy stream, and sync command paths;
- keeps background turns on their existing visible-turn storage;
- strips the closed internal pending-context suffix from user messages normalized from direct Hermes history;
- tests the original visible text in state and the direct-history normalization.

## Follow-up

Codex re-read the full diff and reported:

> No violations found.
>
> The prior high finding is resolved across sync send, legacy stream, sync model-backed command, background turns, direct `/history`, and state fallback: visible Panels text remains the original prompt/message, while pending context is only added to model-bound `prompt.submit`.
>
> Rechecked producer coverage/classification, keyed coalescing, shared `prompt.submit` coverage, exact-revision ack after accepted submit, busy/error/race retention, migration/delete cleanup, and no chat/event-log substitution. No concrete violations found.

## Final integrated review

After serial integration with the concurrent chat-image work and a clean `./verify`, Codex reviewed
the live main-worktree diff against the accepted plan, checklist, and verification matrix. It checked
all producer and delivery paths, failure and image ordering, visible-history behavior, schema cleanup,
and the key-agnostic gateway boundary. Verdict: `NO VIOLATIONS`.
