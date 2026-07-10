# t_hs04 plan review

The independent Codex review found two omissions in the first plan:

1. It did not explicitly include the ticket's one-squashed-commit landing contract.
2. It did not require public `SharedGateway` coverage for child death while a product consequence is
   pending.

Both findings were accepted. The plan now requires the complete hs01-hs04 feature to land as one
commit on `codex/hermes-session-ingress`, excluding unrelated worktree changes. It also requires a
fake-backed child-death test proving one honest unknown/offline settlement, no retry or leaked
waiter/listener, and continued service from the independently configured sibling role gateway.

The focused follow-up review result was:

```text
NO VIOLATIONS
```
