# Codex plan review

Model `gpt-5.5`, read-only sandbox, high reasoning. The full review output was surfaced in the worker transcript.

## Findings and disposition

1. **Unsafe backend responses underspecified** — accepted. Plan now requires `nosniff`, explicit inline allowlist, attachment for HTML/HTM/SVG/Markdown/unknown, and filename from resolved `Path.name`.
2. **HTML sandbox underspecified** — accepted. Plan now requires fetched text assigned only through `srcdoc`, empty sandbox, no `innerHTML`, and script-isolation e2e.
3. **Path normalization incomplete** — accepted. Plan now specifies decoded-path validation, residual/double-encoded cases, strict resolution, `relative_to` containment, directory/non-file rejection, and exact tests.
4. **Hash router incompatible with path segments** — accepted. Preview route is query-based and parsed with `URLSearchParams`, with nested/space filename coverage.
5. **Editable Markdown could serialize preview DOM** — accepted. Hydration is read-only only; edit mode retains ordinary anchors and gets multi-kind round-trip tests.
6. **Frontend contract too ticket-specific** — accepted. Plan now defines a discriminated public target union and central resolver extensible by future target kinds.
7. **Dispatch/final verify conflict** — accepted. Implementer runs focused checks; integrator owns the one final `./verify`.

## Follow-up

Codex re-reviewed the revised plan and dispatch with the same model, sandbox, and reasoning level and returned `NO VIOLATIONS`.
