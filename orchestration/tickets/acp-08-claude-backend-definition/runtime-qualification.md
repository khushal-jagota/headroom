# ACP-08 Claude backend runtime qualification

Audited 2026-07-20 without a model prompt. All package installation, native-process probing, and
Claude state writes were confined to `/private/tmp/acp08-claude-qualification.3d7zQN`; no repository
source, user Claude configuration, or global package installation was changed.

## Result

**READY. Pin `@agentclientprotocol/claude-agent-acp@0.60.0`, not `0.59.0`.** Version `0.60.0` is the
current npm `latest`, preserves the exact compaction controls required by the amended contract, and
updates the embedded runtime to Claude Code `2.1.215`. It also removes a documented session-start
stall from `0.59.0` ([release changelog](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/CHANGELOG.md#L3-L16)).

No activation blocker was found. Authentication still needs the ticket's later authenticated Panels
dogfood because a no-prompt probe cannot prove a paid turn.

## Exact published artifact and closure

The live [npm registry metadata](https://registry.npmjs.org/@agentclientprotocol%2fclaude-agent-acp)
reported `latest=0.60.0`, published at `2026-07-20T12:51:55.179Z`, with git head and release tag
[`0a88de2a`](https://github.com/agentclientprotocol/claude-agent-acp/tree/0a88de2a83d017e0f5f6f429f7edea3700313f3a).
The exact [0.60.0 tarball](https://registry.npmjs.org/@agentclientprotocol/claude-agent-acp/-/claude-agent-acp-0.60.0.tgz)
was downloaded and independently hashed:

- shasum: `981d5baa477c66d8bef75313f29509e4f9abd29e`
- integrity: `sha512-+ZZCJukpKdEY+/O982UCtgGHOY+MKa/JPpZ34v25ITawRyQyg3cqqOGo3M+9TsA4D+T/NXb+kT3zUB1uQZhY+Q==`
- package entrypoint: `dist/index.js`; engine: Node `>=22`

The release manifest freezes ACP SDK `1.2.1` and Claude Agent SDK `0.3.215`
([source](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/package.json#L1-L61)).
The installed Agent SDK manifest records `claudeCodeVersion: 2.1.215` and exact platform packages
`0.3.215`; on this machine the lock selected
`@anthropic-ai/claude-agent-sdk-darwin-arm64@0.3.215` with integrity
`sha512-KIOe3N/ypVIdsI7fnJUHT0Djei1RH01TbxK6LI2mgoHKZ6NVDt/Q9kQ1+On3b/l899RhE6C1SMAiQ0iB4sbkjA==`.
Its native binary printed `2.1.215 (Claude Code)`.

Local qualification used Node `22.22.3` and npm `10.9.8`.

## One collision-free backend lock

A disposable manifest with these two exact root dependencies installed cleanly:

```json
{
  "private": true,
  "dependencies": {
    "@agentclientprotocol/claude-agent-acp": "0.60.0",
    "@agentclientprotocol/codex-acp": "1.1.4"
  }
}
```

`npm ls --all` was clean. Both adapters deduplicated to `@agentclientprotocol/sdk@1.2.1` and
`zod@4.4.3`; Claude additionally locked Agent SDK `0.3.215`, while Codex locked its independent
`@openai/codex@0.144.6`. Therefore `agent_backends/package.json` and `package-lock.json` must be one
shared integration-owned pair. ACP-08 adds the exact Claude root while preserving the exact Codex
root; it must not create or overwrite a Claude-only manifest.

## Exact no-model protocol behavior

The adapter was launched through absolute Node plus the absolute installed `dist/index.js` under an
otherwise empty environment containing only the six contracted inherited names and the two Panels
worker identity variables. `CLAUDE_CODE_EXECUTABLE` and provider keys were absent.

An initialize-only stdio exchange returned:

```json
{
  "protocolVersion": 1,
  "agentInfo": {
    "name": "@agentclientprotocol/claude-agent-acp",
    "title": "Claude Agent",
    "version": "0.60.0"
  },
  "agentCapabilities": {
    "promptCapabilities": { "image": true, "embeddedContext": true },
    "loadSession": true,
    "sessionCapabilities": {
      "additionalDirectories": {}, "close": {}, "delete": {},
      "fork": {}, "list": {}, "resume": {}
    }
  }
}
```

The response also truthfully advertised MCP HTTP/SSE, logout, configurable providers, and private
Claude prompt queueing. These are additional capabilities, not failures of the required preflight
subset. The exact implementation is in the release's
[initialize handler](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L1219-L1366).

Without sending `session/prompt`:

- `session/new` returned one UUID plus modes/config options;
- `session/load` of that UUID in the same process returned the same UUID;
- after process exit, loading that never-prompted UUID returned JSON-RPC `-32002`,
  `Resource not found: <uuid>`; and
- an arbitrary missing UUID returned the same exact error.

This matches upstream: load creates/resumes the SDK query, then awaits history replay before replying
([new/load source](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L1374-L1434));
a new session with no prompt has no persisted transcript for a fresh process to load
([upstream test](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/tests/session-load.test.ts#L102-L126)).
The production preflight is therefore correctly initialize-only: it validates the real executable
without creating a misleading unpersisted session or consuming model work.

## Compaction contract

Release `0.60.0` retains the exact lifecycle already found in the primary-source audit:

- `status: "compacting"` emits `Compacting...`;
- `compact_result: "success"` emits `\n\nCompacting completed.`;
- `compact_result: "failed"` emits `\n\nCompacting failed: <compact_error>`, or
  `\n\nCompacting failed.` when no reason exists; and
- `compact_boundary` only refreshes usage. It is not the completion signal.

The adapter coalesces duplicate terminal SDK status messages with its own in-progress latch
([exact mapping](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L2331-L2403)).
Generated user messages, including the backend-private compacted context, are not forwarded as
conversation text ([filter](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L3437-L3455)).
Nothing in `0.60.0` requires a fork, summary parser, 60-second failure, or Panels-specific context.

## Auth, environment, roots, and reverse services

When `CLAUDE_CODE_EXECUTABLE` is absent, the adapter resolves the native binary from the exact Agent
SDK platform dependency ([resolver](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L849-L889)).
Its native credential store is the OS keychain or Claude config directory
([source](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L1590-L1618));
with `CLAUDE_CONFIG_DIR` scrubbed, that directory defaults to `HOME/.claude`. Missing/expired auth
during a prompt becomes ACP `authRequired` instead of replaying the TUI's `/login` prose
([auth handling](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L995-L1025)).
Anthropic's own setup documentation lists Console, Claude subscription, and enterprise-platform
authentication and states that Claude Code stores the resulting credentials
([official documentation](https://docs.anthropic.com/en/docs/claude-code/getting-started)).

The adapter forwards its process environment to the embedded SDK. Panels must therefore keep the
contracted allowlist rather than inherit provider secrets accidentally. The same creation path:

- preserves the `claude_code` system-prompt preset while accepting the Panels append;
- prefers official ACP `additionalDirectories` and merges them with explicit SDK directories; and
- uses the supplied absolute cwd
  ([session options](https://github.com/agentclientprotocol/claude-agent-acp/blob/0a88de2a83d017e0f5f6f429f7edea3700313f3a/src/acp-agent.ts#L5018-L5229)).

Source inspection still finds no standard ACP terminal reverse method. Bash uses ordinary typed tool
content unless the client opts into the adapter-private `_meta.terminal_output`, which Panels will
not advertise. The adapter has standard permission requests. Its file client methods have no
production call site in this release. The contract's truthful tuple remains
`filesystem=False, terminal=False, permission=True`.

## Reproduction commands

```sh
npm view @agentclientprotocol/claude-agent-acp version dist-tags versions --json
npm view @agentclientprotocol/claude-agent-acp@0.60.0 gitHead dist dependencies engines bin --json
npm view @anthropic-ai/claude-agent-sdk@0.3.215 dist optionalDependencies engines --json
npm install --save-exact @agentclientprotocol/claude-agent-acp@0.60.0 \
  @agentclientprotocol/codex-acp@1.1.4
npm ls @agentclientprotocol/claude-agent-acp @agentclientprotocol/codex-acp \
  @agentclientprotocol/sdk @anthropic-ai/claude-agent-sdk @openai/codex zod --all
node node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js --version
node_modules/@anthropic-ai/claude-agent-sdk-darwin-arm64/claude --version
```

The stdio checks used ordinary ACP JSON-RPC `initialize`, `session/new`, and `session/load` requests
against the same exact entrypoint. No `session/prompt` request was sent.
