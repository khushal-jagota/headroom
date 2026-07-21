# ACP-05 permission diff contract

## Why this exists

Real Hermes dogfood proved that edit approval carries an exact ACP `diff` content block on
`ConversationPermissionRequest.request.toolCall`, while Panels currently renders only the tool title
and options. The user is therefore asked to approve an edit without the supplied change.

## Contract

1. `PermissionPrompt.svelte` renders every `diff` content block supplied on the pending permission's
   exact `toolCall`, in source order, before the permission options.
2. It uses the existing `DiffView.svelte` and its line-number/marker/accessibility behavior. Do not
   add a second diff algorithm or new visual language.
3. A request with no content, or with only non-diff content, remains byte-for-byte equivalent to the
   current compact permission title/options/status UI. Do not expose arbitrary raw input/output.
4. The exact option IDs, kinds, labels, disabled/submitting state, accessibility labelling, and
   `onSelect(requestId, optionId)` behavior do not change.
5. Add focused browser-component evidence for old/new lines and for the no-diff control. Keep the
   original Panels design restrained; this is missing information, not a redesign.

## Allowed files

- `web/src/components/acp/PermissionPrompt.svelte`
- `web/tests/acp-browser-components.test.mjs`
- this ticket's implementation plan/report

## Prohibited

- Backend or wire-contract changes.
- Changes to `DiffView.svelte` or `lineDiff.ts`.
- Raw JSON display, extra approval actions, or ACP-UI styling.
- `./verify`; root reserves the one final canonical run.

## Acceptance

- The new focused component assertions pass.
- Existing ACP browser component tests, Svelte diagnostics, and production frontend build pass.
- One bounded independent review reports no contract violation.
