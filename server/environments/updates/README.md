# Update API

The owner API separates the server attempt from each machine update. All routes require the owner.

An offered release can include `available.compatibility`, with the same shape as `/api/meta` compatibility. These limits belong to the offered release. Older publications omit them, so clients must not substitute the running server's limits for a warning about the offer. The publication receipt writes the limits from the exact published source, beside `parts`.

`client_versions` contains the last retained identity from each device. The card compares those declarations with the offered release's compatibility limits before Update. The ordinary update-status read reconciles new declarations.

`GET /api/updates` retains the server's `state`, `allowed_actions`, `available`, and `attempt`. The installer persists the home outcome. Reading status cannot complete the attempt. Before a successful release check, the status can be `check_unavailable`. A fresh machine report is separate evidence of the running machine version. The legacy `mac_details` field remains for existing clients.

- `machines` describes each registered executor machine against the current attempt, including failed targets that remain eligible for retry.
- `available_machines` describes each machine against the pending offer. Its request and attempt IDs are null because the offer has no machine request yet. It is empty without an offer. A new offer does not remove a retry from `machines`.
- `interruption` describes the pending offer. It contains `stops_agents`, the affected agent names in `agents`, and the affected destinations in `machine_ids`. It is empty without an offer. The server recomputes it from current activity on each read and at the press. The public attempt retains the press result in `attempt.interruption`. It is null for historical attempts without a snapshot. Until the server and home machine run separately, the affected destinations include home work that a server restart stops. A temporarily absent lease does not remove known active work.

Each machine entry contains:

| Field | Meaning |
| --- | --- |
| `machine_id`, `name`, `platform` | Stable destination, display name, and registered operating system. |
| `present` | Whether the machine has a current lease. |
| `build_id` | Last reported content identity, or null for an unknown or historical identity. |
| `target_build_id` | Publication content identity for this platform, or null when unavailable. |
| `changed` | Whether the target requires another machine build. When the publication has a build identity, a missing machine identity counts as changed. Older publications without a target build identity compare the machine’s reported application revision with the release revision. |
| `state` | One of the states below. |
| `attempt_id` | Server attempt that owns this machine target, or null without a target. Use it in the retry URL. It can differ from the top-level attempt after a newer server attempt fails. |
| `request_id` | Current machine request, or null before a request exists. |
| `detail` | Machine error or status explanation, or null. |
| `can_retry` | Whether the owner can retry this machine's current target. |

The machine state values are `current` (no required build change), `available` (a changed build in the pending offer), `waiting` (a retained target for an absent machine), `updating` (a present machine has a target), `failed` (archive preparation fails, or the machine reports failure or rollback), and `unavailable` (the release has no usable artifact for its platform).

`POST /api/updates/attempts/:attempt_id/machines/:machine_id/retry` takes no body. It returns `{attempt, machine}`, with the latest public server attempt and the selected machine's fresh state and request ID. The machine's `attempt_id` identifies its retained target even when the latest server attempt differs. It retains every other machine's request and report. A stale attempt or an ineligible machine returns 409. The old `retry-mac` route delegates only to the original Mac. The server retry route still retries the server attempt.

Contracts live in `contracts.ts`. Executor channel contracts live in `../../executor/wire.schema.json`.

At cutover, accepted sends drain before writes close. The new home waits for compatible, credentialed traffic over its local executor socket while ordinary work stays frozen. The machine retries its lease during the trial. Only the installer can keep the candidate and admit work.
