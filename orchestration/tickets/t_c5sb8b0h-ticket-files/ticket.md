# Ticket t_c5sb8b0h — Support ticket files and centralized previews

## Accepted success

Panels owns ticket files under predictable ticket folders, serves them safely through Panels paths, and gives every Markdown surface one shared preview behavior for Markdown, images, video, sandboxed HTML, ordinary links, and download fallback. The implementation stays portable with the database plus `data/` and does not add artifact records, stage slots, permissions, comments, or dashboard machinery.

## Accepted boundaries

- Canonical ticket notes, fields, proposals, results, and chat remain database text.
- Standalone work products, including `.md`, live under `data/files/tickets/<ticket_id>/...` by default.
- File resolution and rendering decisions are centralized behind a reusable contract/component.
- Direct HTML and unknown files must not execute as Panels pages.
- Inline editing must preserve raw Markdown through edit, save, render, and reopen.
- Every UI slice receives focused automated coverage and live browser exercise as it lands.

## Relevant existing contracts

- FastAPI is wired only in domain `api.py` modules and `src/planner/core/server.py`.
- Markdown rendering is centralized in `assets/markdown.js` and `web/src/components/MarkdownBlock.svelte`.
- Editable Markdown round-trips through `web/src/lib/markdownEdit.ts`.
- `./verify` is the final source of truth; browser behavior belongs in Playwright e2e.
