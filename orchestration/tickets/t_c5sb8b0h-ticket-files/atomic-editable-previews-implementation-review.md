# Atomic editable previews implementation review

Codex `gpt-5.5`, read-only sandbox, high reasoning.

## First review

1. **Escape after a prior local draft save did not restore the original proposal.** Resolved by giving `InlineEdit` an optional cancel value hook and making `ApprovalBlock` reset to `proposalBody`. Browser coverage now blurs one local edit, begins another, presses Escape, and proves the original proposal returns.
2. **Direct active edit → Approve was not proven.** Resolved by choosing scope before editing, making real keyboard edits, and clicking Approve directly. Browser proof confirms exact edited Markdown is accepted.
3. **Entity-bearing exact tokens were not proven through the real DOM.** Resolved with a browser round trip for an emphasized label containing `&` and a query/fragment target. The stored token is byte-identical.
4. **Unmount/abort/iframe cleanup lacked proof.** Resolved with an init-script fetch trap and retained iframe reference. Deleting slots aborts the pending Markdown fetch, disconnects the iframe, and clears `srcdoc`.

## Re-review

The four findings were resolved. Codex then compared `FilePreview` against `HEAD` and called embedded Markdown/HTML expansion a read-only regression. This was refuted: `HEAD` is the older initial ticket implementation, while the accepted deterministic-preview follow-up intentionally changed embedded Markdown to inline rendering and HTML to a sandboxed card before this atomic-editor correction began. The atomic plan's “keep current behavior” means preserve that already-implemented deterministic behavior, not restore the superseded link-only baseline.

No unresolved atomic-editor finding remains.
