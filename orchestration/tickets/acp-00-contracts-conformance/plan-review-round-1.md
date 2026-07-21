# ACP-00 plan review — round 1

## Findings

### 1. Blocker — the plan reserves the required client-side SDK path instead of proving it

`plan.md:18-19` says `acp.stdio.spawn_agent_process` and `ClientSideConnection` are reserved for the
later production child. That contradicts `contract.md:17-19`, which requires ACP-00 to use those
pinned SDK surfaces, and leaves the scripted-subprocess acceptance at `contract.md:204-205` without
proof that the reference client actually uses the official client-side stdio connection. The plan's
agent half is correct: in `agent-client-protocol==0.11.0`, `acp.run_agent(Agent)` creates the
agent-side connection. The matching client half should be explicit: `acp_reference_subject.py`
launches that script with `acp.stdio.spawn_agent_process(client, command, *args, ...)`, drives the
returned `ClientSideConnection`, and observes the raw stream through the SDK's observer seam when
proving protocol-only stdout. Reserve only the production wrapper/composition for ACP-01, not these
SDK APIs or their ACP-00 conformance proof.

### 2. High-value correction — the proposed TypeScript test does not prove that ACP unions remain SDK-owned

`plan.md:109-115` proves that committed JSON fixtures are structurally assignable to the exported
Panels unions, but an implementation that hand-copies `PromptRequest`, `SessionNotification`,
permission, content, tool, or plan unions can pass the same `satisfies` checks. That misses the
explicit no-copy requirement in `contract.md:12-13,140-142` and the named acceptance at
`contract.md:199-201`; the acceptance map at `plan.md:161` currently claims this proof without
specifying one. Add a deterministic source-boundary assertion for `web/src/lib/acp/contracts.ts`:
the ACP payload members must be imported from `@agentclientprotocol/sdk` (and donor state types from
the vendored paths), while local declarations of the authoritative ACP union/type names are
rejected. Keep the fixture/typecheck test as the complementary wire-compatibility proof.

## Verdict

**NOT READY** — resolve finding 1 before implementation; add the missing ownership proof in finding
2 so the named acceptance is executable rather than review-only.
