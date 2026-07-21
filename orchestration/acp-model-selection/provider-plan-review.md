# Provider plan review

Status: **READY**

This narrow re-review checked the revised MR-02, MR-03, and MR-04 plans against
the corrected launch-only contract and the prior Hermes legacy-wire finding.

## Prior finding disposition

**Resolved — MR-02 legacy Hermes wire seam and ownership.** MR-02 now runs after
MR-01, owns an explicit serial allowlist for the separate legacy-model capability,
the SDK raw `session/set_model` implementation, role-skill forwarding, exports,
Hermes adapters/registration, scripted-agent support, focused tests, and evidence.
It also forbids expanding into the generic registry/configuration files or parallel
provider files. This is sufficient to send Hermes's exact method despite Python ACP
SDK `0.11.0` lacking a public stable-model method and its `ext_method()` prefixing
extension names with `_`. No Hermes checkout change is proposed.

## Corrected owner boundary

All three provider plans now configure only the first unbound ACP session, before
the initial binding and first prompt. Each has explicit proof that bound loads,
replacement/recovery children, and later explicit New Conversation paths do not
reapply the historical Kickoff values. Each also treats the stored values as launch
history rather than current agent state and requires the frozen Kickoff controls to
disappear, with no post-Kickoff current-setting display.

MR-03 and MR-04 retain correct semantic-category discovery, refreshed
model-before-reasoning application, temporary-session cleanup, exact-value failure
behavior, provider-local write scopes, and real first-prompt proof. MR-02 retains the
correct read-only current-provider catalog and exact camel-case Hermes wire payload.

No unresolved contract violation, provider API mistake, unsafe file overlap, hidden
Hermes source change, cleanup gap, or first-prompt proof gap remains.
