# launch tokens

The Manager assigns the PR 1029 authentication port to a separate Ticket after the staging merge checkpoint lands.

The merge retains Python's launch-token implementation and schema. The TypeScript runtime does not implement it yet. This includes private launch credentials, Bearer authentication, conversation-bound authority, and executor identity propagation. The schema table and migration frontier are retained for database admission, not evidence of authentication parity.

Exact failing comparison nodes will be recorded from the hosted checkpoint run. Tests remain active until that evidence identifies a precise expected failure. Unrelated parity gaps belong to the second catch-up PR and must not use this name.
