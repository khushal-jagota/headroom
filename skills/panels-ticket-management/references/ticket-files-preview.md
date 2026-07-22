# Ticket files and preview subsystem discussion

Use this reference when discussing or shaping rich media/files in Panels tickets: images, videos, HTML mockups, diagrams, generated files, and links.

## Owner direction from the design discussion

- Do not start by building a broad “artifact system” or per-stage artifact slots.
- Files mostly need to exist where the human is already deciding/reading: ticket text, proposals, results, chat, or future workflow surfaces.
- Querying artifacts globally, reusing them across many places, and complex permissions are not primary needs; Panels is currently for one user.
- Avoid fixed-stage coupling. Future tickets may have variable workflows, so the foundation should not be `plan_artifacts`, `result_artifacts`, etc.
- Keep the conversation concise when discussing this: broad options first, then let the user choose the next detail.

## Working model favored in the discussion

- Store files in Panels-managed filesystem storage, probably ticket-scoped:
  - `data/files/tickets/<ticket_id>/<filename>`
- Serve them only through Panels, so local and future VPS behavior match when the database and `data/` filesystem move together:
  - `/files/tickets/<ticket_id>/<filename>`
- Link to files from normal markdown/text using those Panels-served URLs. Ticket IDs are stable and ticket folders make debugging easy.
- Do not introduce a file-id registry/table unless a real need appears later.

## Preview subsystem

Preview behavior should be centralized in a thin deep module, not reimplemented in each UI surface.

Conceptual contract:

```text
Panels file/link reference -> preview resolver -> small render/open contract
```

The resolver owns:

- type detection;
- image/video inline decisions;
- HTML sandbox/open-in-workspace decisions;
- external/open/download fallbacks;
- any safety flags needed by the UI.

The UI should stay simple: render whatever preview result the resolver returns.

## HTML artifact compatibility before publishing

Panels' preview sandbox is part of the artifact's runtime contract. Before linking an HTML mockup from a ticket:

1. Inspect both the embedded and full-preview iframe permissions. Do not assume an artifact that works at `file://` or at the raw managed URL will behave the same in preview.
2. If scripts are not allowed, do not make meaningful content depend on JavaScript-created DOM, `<template>` cloning, tab switching, or click handlers. Render every decision-relevant option directly in the HTML so the at-rest preview remains useful. Remove controls that imply unavailable interaction; for a static fallback, show all variants in the document and use ordinary anchors only as navigation, not as the sole way to reveal content.
3. Verify the managed link through the real Panels preview route and assert visible paint. For a multi-option mockup, scroll through every option; accessibility-tree text alone is not proof that the iframe painted it.
4. When safe interactivity is itself required, preserve the failing interactive artifact unchanged as a reproduction under the follow-up ticket. Give the current decision ticket a static, script-free fallback rather than blocking review on the subsystem fix.
5. Scope the follow-up to the shared preview contract. A likely safe route may grant scripts while retaining a unique sandbox origin, but treat that as a hypothesis to test—not permission to remove sandboxing or add same-origin, navigation, popup, form, or download capabilities.

This keeps planning artifacts reviewable today while preserving an exact red fixture for the centralized preview repair.

## Debugging HTML preview failures

Treat inline rendering and the dedicated/open-full action as separate behaviors and reproduce both.

For a blank or inert HTML iframe:

1. Reproduce through the real Panels embedded preview and dedicated preview route. Opening the file directly is only a control; it does not prove the product decision surface works.
2. Use a real managed HTML artifact and assert **visual paint**, not only iframe presence, loaded text, or accessibility-tree content. A sandboxed `srcdoc` frame can contain a complete parsed document that accessibility tools can read while Chromium still paints an empty rectangle.
3. Inspect whether decision-critical content is created only by JavaScript—for example, rows cloned from a `<template>` or variants revealed by click handlers—and compare that requirement with the iframe's actual `sandbox` capabilities. An inert selector plus missing runtime-created content is a strong script-capability clue.
4. Inspect the iframe's `srcdoc`, `sandbox`, dimensions, and console/network state. Change one variable in a diagnostic browser probe. If changing only a sandbox capability makes the same content paint or interact, that isolates the boundary—but it is evidence, not permission to remove sandboxing or grant unrelated capabilities.
5. Test the open-full control independently. Inspect its resolved `href`, target, and resulting tab URL. A hash-relative self-link with `target="_blank"` may yield a blank tab even when the embedded preview loaded correctly.
6. Turn the symptoms into deterministic browser regressions: prove a visible marker is painted, prove the intended interaction changes visible state, and prove the new tab reaches the intended managed artifact or preview URL. Keep image, Markdown, video, audio, download, and external-link behavior unchanged.

When ticketing this regression class, include the exact managed artifact, preview-route URL, screenshot, and diagnostic result. Preserve the original interactive artifact as the red fixture; make any immediate static fallback a separate reviewability workaround. Do not merge the repair into a broader embed redesign merely because both touch previews.

## Ticket-shaped wording if this becomes work

Title: Support ticket files and centralized previews

Success: Panels supports files that belong to a ticket and can be linked from ticket text/chat/proposals/results using normal markdown links. Files live in a predictable ticket folder, are served through Panels, and render through one centralized preview path so images/videos/HTML/other files behave consistently. This should work locally and later on a VPS if the database and `data/` filesystem move together.

Approach: Add ticket-file storage and serving first, not a broad artifact system. Store files under `data/files/tickets/<ticket_id>/<filename>` and serve them through Panels at `/files/tickets/<ticket_id>/<filename>`. Text surfaces can link to those URLs normally. Add a deep preview resolver module that takes a Panels file/link URL, identifies what it is, and returns a small UI contract: inline image/video, sandboxed/open HTML, external/open link, or download fallback.
