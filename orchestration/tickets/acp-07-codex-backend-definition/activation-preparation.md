# ACP-07 Codex backend activation preparation

Checked: 2026-07-20 21:55 BST (`2026-07-20T20:55:49Z`)

Scope: official registry/source evidence and disposable no-model process probes only

Verdict: **READY for immediate implementation after the generic selector/catalog slice lands**

No package, process, authentication, or shared-manifest blocker remains. The implementation must
pin `@agentclientprotocol/codex-acp` `1.1.4` in the shared backend manifest and commit the npm v3
lockfile which resolves `@openai/codex` `0.144.6`. The later authenticated Panels and Automatic
Employee dogfood remains an implementation acceptance gate; it was deliberately not attempted here.

## Current official activation candidate

The live official npm registry still reports:

| Artifact | Exact activation result |
| --- | --- |
| `@agentclientprotocol/codex-acp` | `1.1.4` (`latest`) |
| Adapter release commit | `921d466e3aaf747885e395e761cd74ad2d39cd96` |
| Adapter tarball integrity | `sha512-DzusIpGwlQwMWuHgJhU8FWMsyQvzjenB93IEzQATkdbNulo5Rd9GKOz8+B+/C9iWWxmyXgtgmjzaL+iRFyDryQ==` |
| Adapter entrypoint | `dist/index.js` |
| `@agentclientprotocol/sdk` | `1.2.1` |
| Locked `@openai/codex` JS wrapper | `0.144.6` |
| Locked native package on this host | `@openai/codex-darwin-arm64@0.144.6-darwin-arm64` |
| Node used by the probe | `22.22.3` |
| npm used by the probe | `10.9.8` |

Primary evidence:

- The [official npm version document](https://registry.npmjs.org/@agentclientprotocol%2fcodex-acp/1.1.4)
  and live `latest` dist-tag both identify `1.1.4`, its `gitHead`, tarball, integrity, dependencies,
  and `codex-acp: dist/index.js` executable.
- The [official GitHub release](https://github.com/agentclientprotocol/codex-acp/releases/tag/v1.1.4)
  is still the latest release. Its annotated tag resolves to the same
  [`921d466e` release commit](https://github.com/agentclientprotocol/codex-acp/commit/921d466e3aaf747885e395e761cd74ad2d39cd96)
  reported by npm.
- The exact [published source manifest](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/package.json)
  declares `@agentclientprotocol/sdk ^1.2.1` and `@openai/codex ^0.144.4`. A newly generated lock
  therefore resolves the current compatible Codex patch, `0.144.6`; `npm ci` freezes that result.

The adapter repository's own release lock used `@openai/codex` `0.144.4`, while the current shared
production lock candidate resolves `0.144.6` through the published `^0.144.4` range. This is not an
ambiguity at runtime: Panels' committed lockfile, not the adapter repository's development lock, is
the production closure. The disposable adapter on `0.144.6` initialized successfully and its
installed CLI reported `codex-cli 0.144.6`.

## One shared `agent_backends` manifest

A disposable root manifest containing only these exact production dependencies was installed:

```json
{
  "dependencies": {
    "@agentclientprotocol/claude-agent-acp": "0.60.0",
    "@agentclientprotocol/codex-acp": "1.1.4"
  }
}
```

The generated npm v3 lock contained 122 package records. `npm ci` then reproduced it successfully,
`npm ls` returned clean, and npm reported no invalid or conflicting dependency. The load-bearing
shared closure was:

| Package | Exact shared-lock result | Why coexistence is sound |
| --- | --- | --- |
| `@agentclientprotocol/sdk` | one deduplicated `1.2.1` | Codex accepts `^1.2.1`; Claude requires exact `1.2.1` |
| `zod` | one deduplicated `4.4.3` | Satisfies Codex `^4.0.0` and Claude `^3.25.0 || ^4.0.0` |
| `@openai/codex` | `0.144.6` | Codex-only runtime, frozen by the shared lock |
| `@openai/codex-darwin-arm64` | `0.144.6-darwin-arm64` | Exact optional native package for this host |
| `@anthropic-ai/claude-agent-sdk` | `0.3.215` | Claude-only runtime, exact direct adapter dependency |
| `@anthropic-ai/claude-agent-sdk-darwin-arm64` | `0.3.215` | Exact optional native package for this host |

The [official Claude adapter version document](https://registry.npmjs.org/@agentclientprotocol%2fclaude-agent-acp/0.60.0)
pins Claude Agent SDK `0.3.215` and declares Node `>=22`. That SDK package identifies its embedded
Claude Code as `2.1.215`, and its native executable reports `2.1.215 (Claude Code)`. The configured
`22.22.3` executable therefore satisfies the combined runtime's effective Node floor. These are
coexistence facts only—ACP-08 owns Claude's behavioral qualification.

Relevant lock integrities observed in the disposable install:

```text
@agentclientprotocol/sdk@1.2.1
sha512-jwYUdOQR7tc+Zfch53VL4JJyUNK/46q03uUTYb+PjECsmnNl94XFXOfYLJ8RBpMNidXd1rpOAVgb0vqD98xImA==

@openai/codex@0.144.6
sha512-wk+2CWiBNXiJLBoN2D08N9RceWkSBnlgk5g2K1a4CXrP/C0gdlHyRUG7RFzm9y41DCK/7tvCct233JVxyFmznw==

@openai/codex-darwin-arm64@0.144.6-darwin-arm64
sha512-6zgvh70MzBNSeT17HEhSOrmmGGZGAKzSC7x6JAq+edkJkdPYA9P0I1tG7aJ49GlBkBxuC+MKBH1qm6+2Cghcww==

@anthropic-ai/claude-agent-sdk@0.3.215
sha512-fBktJCwfu8ZeOSnnSLcWVkIHyp/pjouJsGVGtRnQ0HkcRuOReIbRcB/6n2O5dYV331Ok+MfdGHiPaTORj7pCtQ==

@anthropic-ai/claude-agent-sdk-darwin-arm64@0.3.215
sha512-KIOe3N/ypVIdsI7fnJUHT0Djei1RH01TbxK6LI2mgoHKZ6NVDt/Q9kQ1+On3b/l899RhE6C1SMAiQ0iB4sbkjA==
```

Implementation implication: use one `agent_backends/package.json` and one
`agent_backends/package-lock.json`. List the two adapter packages as exact root dependencies and let
the lockfile own every transitive package. Do not create per-backend Node roots, duplicate the ACP
SDK, add a root `zod` pin, or add a root `@openai/codex` pin merely to restate lockfile data.
Because those two files are shared by ACP-07 and ACP-08, one integration owner should create/update
them serially; the two backend implementation lanes must not each write a one-adapter manifest.

## Exact Codex process contract reverified

The shared disposable install resolved:

```text
Node executable:
/Users/khushaljagota/.hermes/node/bin/node

Adapter entrypoint:
/private/tmp/panels-agent-backends-qualification.lsdlyG/node_modules/@agentclientprotocol/codex-acp/dist/index.js

Adapter version output:
@agentclientprotocol/codex-acp 1.1.4

Bundled Codex version output:
codex-cli 0.144.6
```

The production argv remains exactly `(absolute_node_executable,
absolute_agent_backends/node_modules/@agentclientprotocol/codex-acp/dist/index.js)`. With
`CODEX_PATH` absent, the adapter resolves its own installed `@openai/codex/bin/codex.js` and spawns
it with `process.execPath` plus `app-server`; this is the exact behavior in the
[release source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexJsonRpcConnection.ts#L15-L26).

The no-model probe supplied only the six SDK default inherited names plus the contracted Codex
values:

```text
APP_SERVER_LOGS
CODEX_HOME
DEFAULT_AUTH_REQUEST={"methodId":"api-key"}
HOME
INITIAL_AGENT_MODE=agent
LOGNAME
NO_BROWSER=1
PATH
SHELL
TERM
USER
```

`CODEX_PATH`, `CODEX_CONFIG`, both API-key variables, model-provider controls, and ambient browser
controls were absent. The adapter wrote logs only to the disposable absolute `APP_SERVER_LOGS`
directory and emitted no stderr. `INITIAL_AGENT_MODE=agent` is the adapter's workspace-write,
on-request-approval preset in the exact release source; `NO_BROWSER=1` suppresses the ChatGPT browser
auth method while leaving an already authenticated Codex account readable.

## Initialize and missing-auth boundary

The exact absolute argv was launched from the shared install with a fresh isolated `CODEX_HOME` and
no API key. `initialize` returned:

```json
{
  "protocolVersion": 1,
  "agentInfo": {
    "name": "@agentclientprotocol/codex-acp",
    "title": "Codex",
    "version": "1.1.4"
  },
  "agentCapabilities": {
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
    }
  },
  "authMethods": [
    {
      "id": "api-key",
      "name": "API Key"
    }
  ]
}
```

Safe `session/list`, `session/new`, and missing-ID `session/load` probes all failed before session
creation with the same exact production-style response:

```json
{
  "code": -32603,
  "message": "Internal error: CODEX_API_KEY or OPENAI_API_KEY is not set",
  "data": {
    "envVars": ["CODEX_API_KEY", "OPENAI_API_KEY"]
  }
}
```

This differs deliberately from the earlier `-32000 Authentication required` observation: that
earlier probe omitted `DEFAULT_AUTH_REQUEST`, while the production contract supplies the API-key
default. The current result is the exact expected visible failure when no stored Codex account and no
inherited key exists. Authorization is checked before thread start/resume, so no ACP session can be
bound on this failure. The adapter first checks its app-server account and only invokes the default
auth request when that account still requires authentication
([authorization source](https://github.com/agentclientprotocol/codex-acp/blob/921d466e3aaf747885e395e761cd74ad2d39cd96/src/CodexAcpServer.ts#L378-L406)).

No browser opened, no global Codex home or configuration was read or changed, no credential value was
printed, no thread was created, and no model prompt was sent.

## Compaction and source-contract reconciliation

No authenticated compaction was attempted. The exact package/source facts remain unchanged from the
[primary-source compaction audit](../../acp-migration/compaction-primary-source-audit.md): Codex
emits namespaced `_meta.contextCompaction=true` tool start/completion updates, waits for the
asynchronous same-thread completion before the `/compact` prompt settles, exposes no summary, and
does not require a fork. The implementation should normalize those lifecycle events and exact
command/process errors only. It should not parse fixed prose, inspect private Codex files, or replace
the durable session binding.

The live-plan/stored-`Plan:` presentation difference is likewise unchanged and remains the one named
accepted upstream limitation. It does not change worker execution readiness.

## Handoff

**Status: READY once the generic ACP-07 selector/catalog integration gate lands.**

The Codex implementation should now:

1. add exact `@agentclientprotocol/codex-acp: 1.1.4` to the one shared backend manifest alongside
   exact Claude `0.60.0`;
2. commit the generated npm v3 lock which currently freezes SDK `1.2.1`, Codex `0.144.6`, and the
   host-specific native package;
3. resolve the absolute Node and adapter entrypoint, use the contracted confined environment, and
   require the exact initialize identity/capabilities above; and
4. retain the authenticated human/Automatic Employee continuity, permission, cancellation, replay,
   and compaction dogfood as acceptance gates before calling the backend complete.

## Commands and mutation boundary

The evidence came from live `npm view`, official GitHub API/raw-source reads, and one disposable
installation under `/tmp`. The shared install ran `npm install --save-exact`, `npm ci`, `npm ls`, the
two package version commands, and one NDJSON stdio initialize/list/new/load probe. Product source,
shared package files, authentication/configuration, broad tests, and `./verify` were untouched.
