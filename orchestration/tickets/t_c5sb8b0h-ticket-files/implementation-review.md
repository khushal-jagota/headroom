# Codex implementation review

Model `gpt-5.5`, read-only sandbox, high reasoning. Full review and follow-ups were surfaced in the worker transcript.

## Findings and disposition

1. **Frontend hash targets accepted dot segments** — fixed by central `ticketFileTarget` validation used by the route and resolver.
2. **Cross-origin URLs with `/files/tickets/` paths were treated as local** — fixed by same-origin validation before local target construction.
3. **Backend inline policy allowed every `audio/*` and `video/*` MIME** — fixed with an explicit MIME allowlist and attachment tests for non-allowlisted audio/video types.
4. **First follow-up: `URL` normalized raw/encoded dot segments before validation** — fixed by using `URL` only for origin comparison while extracting and validating the raw pathname spelling.

Each fix was covered by a failing focused test before implementation and a passing focused test afterward.

## Final follow-up

Codex re-reviewed the remaining normalization finding and returned `NO VIOLATIONS`.
