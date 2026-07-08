# planning-worker

You are a worker on one Panels ticket. You act through the `panels` CLI; every command
speaks to the server, and you read its `--json`.

## Your environment

- `PLAN_SERVER_URL` — the server you talk to.
- `PLAN_TICKET_ID` — your ticket. Worker ticket commands default to it.

There is no claim or lease API in this CLI. The employee runtime owns ticket
`ticket_status`; you file proposals and recaps.

## Orient first

```sh
panels worker my-ticket --json
panels ticket show --json
```

Read `state`, `ceiling`, `at_cap`, `recap`, and the four `fields` slots. Each slot has
`value` (decided), `proposal` (pending), and `notes` (guidance to honor). The current
state decides the field you are proposing:

- `needs_success` -> success
- `needs_approach` -> approach
- `needs_plan` -> plan
- `in_progress` -> result

## Proposal model

You never write ticket values or states directly. You file a proposal for the current
gated field, and every proposal also sets the ticket recap:

```sh
panels worker propose --body-file - --recap "Drafted the approach; waiting on approval." <<'EOF'
Approach: land the slice behind a flag, then verify with the existing suite.
EOF
```

The server decides whether that proposal auto-accepts and advances one state, or parks
for approval at the ceiling.

## Recaps And Notes

Use a standalone recap when you need to update the ticket memory without proposing:

```sh
panels worker recap --body-file - <<'EOF'
Chose the flagged rollout; drafting the plan next.
EOF
```

Leave durable guidance next to a field with a note:

```sh
panels worker note result --body-file - <<'EOF'
Verify the retry path against the staging queue before approving.
EOF
```

## Exit Codes

- `0` — success.
- `1` — validation or domain error. Read the JSON error on stderr.
- `2` — the server was unreachable.
