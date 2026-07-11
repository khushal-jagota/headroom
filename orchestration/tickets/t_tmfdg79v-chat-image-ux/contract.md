# t_tmfdg79v — Chat image attachment UX

## Contract

Improve the existing shared chat composer so users can select, drag, or paste one or more images, inspect compact pending previews, remove individual images, and send the remaining ordered images. Preserve text, slash-command, draft, pause, send, transcript, and reload behavior.

## Existing system to extend

- `web/src/components/ChatComposer.svelte`: shared picker and single pending `File`.
- `web/src/components/ChatPanel.svelte`: uploads the pending image and starts the turn.
- `src/planner/chat/contracts.py`, `api.py`, and `service.py`: one managed image reference/path.
- `src/planner/minds/sessions/service.py`: native `image.attach` before `prompt.submit`, with detach on rejection.
- Panels-managed chat storage and centralized `chat-file` previews remain canonical.

## Approved plan, made executable

1. Follow both the served ticket-owned artifact and the tracked review copy at `mockup.html`: compact preview row inside the existing `.chat-box` before the textarea, composer-only drag target, image control still directly beside `/`, and unchanged text, pause, and send controls.
2. Use one ordered pending-image collection and one validation/intake path for picker, drop, and paste. Revoke local preview URLs on removal, successful send, or teardown; retain them after failure.
3. Migrate every singular image seam to an ordered collection: `ChatComposer` and `ChatPanel`, `StartChatTurnBody`, `ChatTurnRequest`, API validation, chat service resolution/visible Markdown, `GatewayAdapter` and every implementation/fake, and live session submission. The public request field is `image_references`; each reference is validated and resolved before the turn is created, and visible Markdown preserves reference order.
4. Upload remaining images through the existing entity-scoped endpoint, submit their ordered managed references, and render them through the existing preview seam. Hermes attaches each resolved path in order before one `prompt.submit`. If any attach fails, no prompt is submitted. If prompt submission is rejected or has an invalid disposition, detach every image attached for that attempt before another prompt may enter. Preserve image-only, text-plus-image, slash-command exclusion/retention, and existing upload-error behavior.
5. Cover frontend intake/removal/object-URL lifecycle, all protocol implementations and fakes, all-reference prevalidation, visible Markdown order, ordered native attach-before-prompt behavior, no-prompt-on-attach-failure, complete detach-on-submit-failure, and real browser picker/paste/drop/removal/multiple-send/reload/error flows.
6. Update docs, independently review, and finish with one clean `./verify`.

## Boundaries

- No new upload or preview subsystem.
- No composer redesign.
- No merge into `main` during Implementation; Closeout owns integration.
- Ticket branch: `ticket/t_tmfdg79v-chat-image-ux` from `9ab2111`.
