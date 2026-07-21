# ACP-07 Codex backend — latest official qualification

Date checked: 2026-07-20 (Europe/London)

## Verdict

**READY FOR RUNTIME QUALIFICATION. There is no newer official published pin.**

The npm registry's `latest` dist-tag and the official repository's latest release both identify
`@agentclientprotocol/codex-acp` **1.1.4**. Exact source inspection at the release commit confirms
that 1.1.4:

- advertises load but neither advertises nor handles ACP `session/fork`;
- emits a live plan as typed ACP `plan` but replays a stored plan as assistant prose; and
- emits compaction lifecycle/status without a non-empty compaction summary.

The first and third facts are compatible with the owner-amended lifecycle-only compaction contract:
Codex compacts the same thread, ACP requires neither fork nor summary, and Panels exposes no backend
context. The stored-plan flattening is accepted as one exact upstream refresh-presentation limitation:
it does not break the durable worker session or automatic work, and Panels will neither parse it nor
read private state to improve it. Version 1.1.4 may proceed to the real runtime/session/permission
qualification before registration.

## Latest-version and artifact evidence

The authoritative npm query returned:

```text
version: 1.1.4
dist-tags.latest: 1.1.4
published: 2026-07-15T20:35:57.608Z
gitHead: 921d466e3aaf747885e395e761cd74ad2d39cd96
tarball: https://registry.npmjs.org/@agentclientprotocol/codex-acp/-/codex-acp-1.1.4.tgz
integrity: sha512-DzusIpGwlQwMWuHgJhU8FWMsyQvzjenB93IEzQATkdbNulo5Rd9GKOz8+B+/C9iWWxmyXgtgmjzaL+iRFyDryQ==
shasum: 6f75b0c0138e83891dc6538ce0f6e717e81fa959
```

The [official GitHub release](https://github.com/agentclientprotocol/codex-acp/releases/tag/v1.1.4)
also marks v1.1.4 as `Latest`, dated 2026-07-15, and points to commit
[`921d466e`](https://github.com/agentclientprotocol/codex-acp/commit/921d466e3aaf747885e395e761cd74ad2d39cd96).
The checked-out annotated tag `v1.1.4^{commit}` resolves to that same commit. No npm version later
than 1.1.4 is listed in the registry metadata.

The official tarball was downloaded to a temporary directory, not installed. Its locally computed
digests exactly matched both registry values above. It contains only `LICENSE`, `README.md`,
`package.json`, and `dist/index.js`.

The published manifest declares
[`@agentclientprotocol/sdk ^1.2.1` and `@openai/codex ^0.144.4`](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/package.json#L42-L49).
The release commit's authoritative lockfile resolves the SDK to
[`1.2.1`](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/package-lock.json#L31-L38)
and `@openai/codex` to
[`0.144.4`](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/package-lock.json#L931-L949).
The npm tarball does not itself ship that lockfile, so Panels would still need its own exact
production lockfile as required by the contract if a future adapter release qualifies.

Primary artifacts:

- [npm registry package metadata](https://registry.npmjs.org/@agentclientprotocol%2fcodex-acp)
- [exact npm tarball](https://registry.npmjs.org/@agentclientprotocol/codex-acp/-/codex-acp-1.1.4.tgz)
- [official v1.1.4 release](https://github.com/agentclientprotocol/codex-acp/releases/tag/v1.1.4)
- [exact v1.1.4 source commit](https://github.com/agentclientprotocol/codex-acp/tree/921d466e3aaf747885e395e761cd74ad2d39cd96)

## Frozen-contract comparison

### Initialize identity and load/fork capabilities

The adapter returns ACP's protocol version and package-derived identity/version. It advertises
`loadSession: true`; the session capability object contains resume, list, close, delete, and
additional directories, but no fork capability
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L201-L241)).
The stdio request router registers new/load/list/delete/resume/close and other session methods, but
does not register `session/fork`
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/index.ts#L111-L141)).

Result: identity/version/protocol and load are compatible. Fork is absent and not required: Codex
compacts its existing thread, while Hermes alone needs a proven persistence fork workaround.

### Stored-versus-live typed plan replay

Live `turn/plan/updated` notifications are mapped to a typed ACP `plan` update with structured
entries
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexEventHandler.ts#L551-L561)).
During `session/load`, a stored `plan` thread item instead becomes `agent_message_chunk` whose text is
`Plan:\n${item.text}`
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1178-L1228),
[conversion](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1278-L1288)).

Result: **accepted pinned limitation**. The implementation must assert this exact difference so an
upstream improvement or regression is visible. It is not a worker-functionality gate and Panels adds
no prose parser.

### Compaction lifecycle

The adapter maps context-compaction start/completion to ACP tool-call updates with only id, title,
status, kind, and `_meta.contextCompaction=true`
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexToolCallMapper.ts#L227-L263)).
The corresponding app-server thread item has only an `id`; the deprecated `thread/compacted`
notification has only `threadId` and `turnId`
([item type](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/app-server/v2/ThreadItem.ts#L108),
[notification type](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/app-server/v2/ContextCompactedNotification.ts#L1-L8)).
The deprecated notification is translated to the fixed assistant message “Context compacted to fit
the model's context window,” not to a summary
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexEventHandler.ts#L171-L172),
[conversion](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexEventHandler.ts#L431-L433)).

Result: **pass** under the owner-amended contract. The stable lifecycle is detectable, completion may
arrive asynchronously, and no summary is required or displayed.

### Load callback ordering

The low-level load sequence is `thread/resume`, subscription callback, then `thread/read` with full
turns
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpClient.ts#L346-L372)).
At the server boundary, `loadSession` waits for session setup, then waits for `streamThreadHistory`,
and only then returns its response
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L507-L527)).
History streaming awaits every ACP session update in sequence
([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1092-L1115)).

Result: source order is compatible with the contract's update-before-load-response requirement.
That does not repair the wrong stored plan type.

### Permission ordering and cancellation

Permission routing is materially compatible in source:

- command permission options preserve allow-once, allow-for-session, optional exact amendments, then
  reject order; file-change options preserve allow-once, allow-for-session, reject order
  ([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexApprovalHandler.ts#L254-L338));
- broader permission options preserve allow-for-session, allow-once, reject order, and cancelled or
  failed permission requests return no grants
  ([source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexApprovalHandler.ts#L97-L117),
  [request](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexApprovalHandler.ts#L154-L193),
  [conversion](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexApprovalHandler.ts#L226-L251));
- each reverse permission request receives the active prompt's cancellation signal, and permission
  handling waits for earlier queued session notifications
  ([signal](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexApprovalHandler.ts#L46-L70),
  [ordering](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpClient.ts#L612-L652)); and
- ACP `session/cancel` is registered and calls Codex `turnInterrupt`; the prompt waits for queued
  session notifications and returns `stopReason: cancelled` when Codex reports interruption
  ([router](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/index.ts#L135-L136),
  [cancel](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1873-L1882),
  [settlement](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1762-L1794)).

Result: no additional source-level blocker was found in option ordering or the ordinary cancel path.
These paths remain **unqualified**, not proven, because this audit was read-only. The implementation
must pass the common permission-settlement, callback-order, cancel, replacement, human-chat, and real
automatic-work probes before the adapter is registered rather than inheriting credit from static
inspection.

## Commands and scope

Read-only commands used (secrets and environment values were not printed):

```sh
npm view @agentclientprotocol/codex-acp version dist-tags versions time repository dist dependencies engines bin license --json
npm view @agentclientprotocol/codex-acp@1.1.4 gitHead dist.tarball dist.integrity dist.shasum --json

QUAL_DIR=$(mktemp -d /tmp/panels-codex-acp-qualification.XXXXXX)
curl --fail --silent --show-error --location \
  https://registry.npmjs.org/@agentclientprotocol/codex-acp/-/codex-acp-1.1.4.tgz \
  --output "$QUAL_DIR/codex-acp-1.1.4.tgz"
openssl dgst -sha512 -binary "$QUAL_DIR/codex-acp-1.1.4.tgz" | openssl base64 -A
openssl dgst -sha1 "$QUAL_DIR/codex-acp-1.1.4.tgz"
tar -tzf "$QUAL_DIR/codex-acp-1.1.4.tgz"

QUAL_REPO=$(mktemp -d /tmp/panels-codex-acp-source.XXXXXX)
git clone --filter=blob:none https://github.com/agentclientprotocol/codex-acp.git "$QUAL_REPO"
git -C "$QUAL_REPO" checkout 921d466e3aaf747885e395e761cd74ad2d39cd96
git -C "$QUAL_REPO" rev-parse v1.1.4^{commit}
```

Source was inspected with `rg`, `sed`, and `nl`. No package was installed into Panels or globally;
no `npm install`, `npm ci`, `npx`, model call, product test, `./verify`, product-source edit, memory-file
edit, or Hermes access occurred. Temporary artifacts stayed under `/tmp`.
