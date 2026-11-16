# launch tokens

The Manager assigns the PR 1029 authentication port to a separate Ticket after the staging merge checkpoint lands.

The merge retains Python's launch-token implementation and schema. The TypeScript runtime does not implement it yet. This includes private launch credentials, Bearer authentication, conversation-bound authority, and executor identity propagation. The schema table and migration frontier are retained for database admission, not evidence of authentication parity.

Retained cases: 19, listed individually in [checkpoint.json](checkpoint.json).

Evidence: [hosted checkpoint run](https://github.com/khushal-jagota/headroom/actions/runs/43904665539). Each marked case failed in this run. Failures cover credential-aware launch environments, canonical-principal routing, generic conversation identity refusal, role-only calls, recovery guidance, and the new credential-carrying CLI command. The declarations adapter also encounters that command's new file parameter.

Cases remain executable strict expected failures. An unexpected pass fails the gate and requires removal of its entry. Unrelated parity gaps belong to the second catch-up PR and do not use this name.

The cleanup CLI case now passes its owner JSON/text comparisons. Its denied-caller leg exposes a distinct launch-token boundary: the new Python CLI sends Bearer credentials that the pre-token TypeScript API cannot authenticate. [Run43916363997](https://github.com/khushal-jagota/headroom/actions/runs/43916363997) records this newly reached failure. The legacy cleanup proof mismatch is closed.
