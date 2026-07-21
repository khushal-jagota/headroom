# Codex ACP 1.1.4 runtime prequalification

Date: 2026-07-20  
Candidate: `@agentclientprotocol/codex-acp@1.1.4`  
Result: **runtime and protocol initialization qualified; authenticated session work remains for the
later implementation/dogfood proof**.

This check used only a disposable npm installation, the published package artifacts, the package's
pinned source revision and upstream test fixtures. It did not use a model, prompt for authentication,
read or change the user's Codex configuration, or edit a product manifest/lockfile.

## Exact disposable installation

Commands:

```sh
probe_dir=$(mktemp -d /tmp/panels-codex-acp-XXXXXX)
cd "$probe_dir"
npm init --yes
npm install --save-exact @agentclientprotocol/codex-acp@1.1.4

node --version
npm --version
npm ls --all --depth=1 \
  @agentclientprotocol/codex-acp \
  @agentclientprotocol/sdk \
  @openai/codex
node_modules/.bin/codex-acp --version
node_modules/.bin/codex --version
```

The generated npm v3 lock resolved:

| Artifact | Exact result |
| --- | --- |
| Node | `v22.22.3`, `darwin-arm64` |
| npm | `10.9.8` |
| Adapter | `@agentclientprotocol/codex-acp@1.1.4` |
| ACP SDK | `@agentclientprotocol/sdk@1.2.1` |
| Codex JS wrapper | `@openai/codex@0.144.6` |
| Codex native package | `@openai/codex@0.144.6-darwin-arm64` |
| Adapter source revision | [`921d466e`](https://github.com/agentclientprotocol/codex-acp/tree/921d466e3aaf747885e395e761cd74ad2d39cd96) |

The npm registry reports `921d466e3aaf747885e395e761cd74ad2d39cd96` as the adapter package's
`gitHead`. Its published tarball and lock integrity were:

```text
https://registry.npmjs.org/@agentclientprotocol/codex-acp/-/codex-acp-1.1.4.tgz
sha512-DzusIpGwlQwMWuHgJhU8FWMsyQvzjenB93IEzQATkdbNulo5Rd9GKOz8+B+/C9iWWxmyXgtgmjzaL+iRFyDryQ==

https://registry.npmjs.org/@openai/codex/-/codex-0.144.6.tgz
sha512-wk+2CWiBNXiJLBoN2D08N9RceWkSBnlgk5g2K1a4CXrP/C0gdlHyRUG7RFzm9y41DCK/7tvCct233JVxyFmznw==
```

The adapter package itself declares `@openai/codex: ^0.144.4`, not an exact transitive version
([published source package manifest](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/package.json#L63-L69)). Therefore
`npx -y @agentclientprotocol/codex-acp@1.1.4` does **not** by itself freeze the Codex patch release.
The ACP-07 production manifest and lockfile are necessary; runtime should execute the locked install
after `npm ci`.

## Exact executable paths

The disposable root was `/private/tmp/panels-codex-acp-RjN3Xd`. Resolved paths were:

```text
Node command:       /Users/khushaljagota/.local/bin/node
Node process path:  /Users/khushaljagota/.hermes/node/bin/node
Adapter bin link:   /private/tmp/panels-codex-acp-RjN3Xd/node_modules/.bin/codex-acp
Adapter entrypoint: /private/tmp/panels-codex-acp-RjN3Xd/node_modules/@agentclientprotocol/codex-acp/dist/index.js
Codex bin link:     /private/tmp/panels-codex-acp-RjN3Xd/node_modules/.bin/codex
Codex JS wrapper:   /private/tmp/panels-codex-acp-RjN3Xd/node_modules/@openai/codex/bin/codex.js
Codex native bin:   /private/tmp/panels-codex-acp-RjN3Xd/node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex
```

Observed version output:

```text
@agentclientprotocol/codex-acp 1.1.4
codex-cli 0.144.6
```

With `CODEX_PATH` absent, the adapter resolves its installed `@openai/codex/bin/codex.js` and spawns
it with the same Node executable plus `app-server`
([adapter app-server spawn](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexJsonRpcConnection.ts#L15-L26)). This supports the
contract's exact argv: an absolute Node executable and the locked adapter `dist/index.js`, with no
ambient Codex command.

## No-model ACP probe

The probe launched the installed adapter over NDJSON stdio with:

```text
CODEX_HOME=/private/tmp/panels-codex-acp-RjN3Xd/isolated-codex-home
APP_SERVER_LOGS=/private/tmp/panels-codex-acp-RjN3Xd/app-server-logs
NO_BROWSER=1
```

`CODEX_HOME` was a fresh disposable directory. No API key, default auth request, custom model
provider, Codex config or Codex path was supplied.

### Initialize: pass

Request:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1,"clientCapabilities":{},"clientInfo":{"name":"panels-runtime-prequalification","version":"1"}}}
```

Relevant exact response:

```json
{
  "protocolVersion": 1,
  "agentInfo": {
    "name": "@agentclientprotocol/codex-acp",
    "title": "Codex",
    "version": "1.1.4"
  },
  "agentCapabilities": {
    "auth": { "logout": {} },
    "providers": {},
    "loadSession": true,
    "promptCapabilities": {
      "embeddedContext": true,
      "image": true
    },
    "sessionCapabilities": {
      "resume": {},
      "list": {},
      "close": {},
      "delete": {},
      "additionalDirectories": {}
    },
    "mcpCapabilities": {
      "acp": false,
      "http": true,
      "sse": false
    }
  },
  "authMethods": [
    {
      "id": "api-key",
      "name": "API Key",
      "description": "Use an API key to authenticate",
      "_meta": { "api-key": { "provider": "openai" } }
    }
  ]
}
```

The app-server log independently identified itself as
`Codex Desktop/0.144.6 ... arm64`, returned the disposable `codexHome`, and produced no stderr.

### List/new/load: exact authentication boundary

The following safe requests were attempted without credentials:

```json
{"jsonrpc":"2.0","id":2,"method":"session/list","params":{"cwd":"/private/tmp/panels-codex-acp-RjN3Xd"}}
{"jsonrpc":"2.0","id":3,"method":"session/new","params":{"cwd":"/private/tmp/panels-codex-acp-RjN3Xd","mcpServers":[]}}
{"jsonrpc":"2.0","id":4,"method":"session/load","params":{"sessionId":"00000000-0000-0000-0000-000000000000","cwd":"/private/tmp/panels-codex-acp-RjN3Xd","mcpServers":[]}}
```

All three returned exactly:

```json
{"jsonrpc":"2.0","id":N,"error":{"code":-32000,"message":"Authentication required"}}
```

The Codex app server reported `account: null` and `requiresOpenaiAuth: true`. The adapter checks
authorization before new/resume session construction and before load/resume/thread-read work
([new/resume gate](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L378-L406), [load gate](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L993-L1016)). No thread was created or
loaded and no model call occurred.

This is the expected fail-closed boundary, not an adapter qualification failure. The later real proof
must supply the contract's explicit API-key/default-auth environment or an existing authenticated
`CODEX_HOME`. This prequalification did not prompt, open a browser or alter user authentication.

## Same-thread compaction contract

Live compaction could not safely be invoked because no authenticated session could be created. The
published 1.1.4 source and its own captured fixtures are nevertheless exact and sufficient to freeze
the strategy input shape.

The adapter maps a Codex `contextCompaction` item to these ACP updates
([mapping source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexToolCallMapper.ts#L227-L263), [upstream captured fixture](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/__tests__/CodexACPAgent/data/context-compaction-lifecycle.json)):

```json
{
  "sessionId": "test-session-id",
  "update": {
    "sessionUpdate": "tool_call",
    "toolCallId": "context-compaction-id",
    "kind": "other",
    "title": "Context compacting",
    "status": "in_progress",
    "_meta": { "contextCompaction": true }
  }
}
```

```json
{
  "sessionId": "test-session-id",
  "update": {
    "sessionUpdate": "tool_call_update",
    "toolCallId": "context-compaction-id",
    "title": "Context compacted",
    "status": "completed",
    "_meta": { "contextCompaction": true }
  }
}
```

The start and completion carry the same ACP `sessionId` and `toolCallId`. The stable observer key is
the adapter's exact `_meta.contextCompaction === true`, not title or prose matching.

For explicit `/compact`, the adapter invokes `runCompact(sessionId)`, which sends
`thread/compact/start` with `threadId: sessionId` and waits for a same-thread asynchronous
`thread/compacted` or completed `contextCompaction` notification before the prompt returns
([command dispatch](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexCommands.ts#L190-L216), [same-thread invocation](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpClient.ts#L425-L427), [completion waiter](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAppServerClient.ts#L497-L500), [completion predicate](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAppServerClient.ts#L1030-L1035)). The upstream test explicitly proves the prompt remains pending until that notification
([completion test](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/__tests__/CodexACPAgent/CodexAcpClient.test.ts#L1519-L1550)). A rejected start or child/process error rejects the command; Panels should preserve that reason.

The adapter also maps legacy `thread/compacted` to fixed `agent_message_chunk` prose
([fixture](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/__tests__/CodexACPAgent/data/thread-compacted.json)). The
Codex strategy should not parse that prose. Explicit command success is already proven by the awaited
command completion; automatic lifecycle uses the tagged tool updates above. No fork, summary or
session replacement is involved.

## Accepted stored-plan presentation limitation

Live `turn/plan/updated` is mapped to ACP `sessionUpdate: "plan"`
([live mapping](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexEventHandler.ts#L551-L562)). During stored history replay, a Codex item of type `plan` is instead mapped exactly to:

```json
{
  "sessionUpdate": "agent_message_chunk",
  "content": {
    "type": "text",
    "text": "Plan:\n<stored plan text>"
  }
}
```

That behavior is direct source, not an inference
([stored replay mapping](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L1224-L1288)). Per the owner decision,
this is an accepted upstream refresh-presentation limitation. Panels must test the exact difference
and must not add a prose parser or treat it as a worker-functionality failure.

## Implementation handoff

ACP-07 may proceed with these frozen facts:

1. Own a production npm manifest and v3 lock; install with `npm ci`. Do not use runtime `npx`.
2. Resolve the absolute Node executable and locked adapter `dist/index.js`; keep `CODEX_PATH` absent.
3. Require initialize identity `@agentclientprotocol/codex-acp`, version `1.1.4`, protocol v1 and
   `loadSession=true` exactly.
4. Treat `-32000 Authentication required` before binding as the expected visible missing-auth
   failure. Do not fall back to another session/backend or open a browser.
5. Normalize only `_meta.contextCompaction: true` start/completion plus explicit command
   completion/error. Keep the durable session ID unchanged.
6. Freeze the stored-plan-as-`agent_message_chunk` difference in a qualification test without a
   parser.

The bounded prequalification itself used no product tests. After this note was written, the research
agent accidentally invoked `./verify` by placing the literal Markdown text ``No `./verify` `` inside a
double-quoted shell status command, causing shell command substitution. The command completed against
the concurrently unfinished ACP-06 worktree and failed on its expected half-converted Python
imports/Ruff state; its frontend build/tests passed. That run is not qualification or release
evidence. Its only observed generated side effects were the current `web/dist` build and gitignored
`data/verify/unit.xml` / `e2e.xml`; root will replace the build at the planned settled integration
step and will not rerun canonical verification before ACP-10.
