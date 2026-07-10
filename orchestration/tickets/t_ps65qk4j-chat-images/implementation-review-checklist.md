# Implementation review checklist

Use after the delegated implementation settles.

- Upload bytes are size-bounded during streaming, sniffed before publication, assigned a sniffed-type extension, atomically renamed from a non-served temp location, and leave no temp/published file on failure. Test fixtures themselves must be structurally valid; do not weaken the sniffer to accept the legacy browser-tolerated malformed PNG/WebP samples.
- Upload and turn-start routes are human-only and validate a real ticket/day/Chief entity.
- Turn start rejects cross-entity, ticket-file, external, missing, and unsafe image references before creating a chat turn.
- Panels stores a canonical chat-file Markdown reference; the actual Hermes session receives the absolute managed path through `image.attach` before `prompt.submit`.
- If `prompt.submit` fails after `image.attach`, use Hermes `image.detach` (or equivalent) so a queued stale image cannot leak into the next prompt.
- Image-only and text-plus-image sends work; ordinary text and slash-command sends remain byte-for-byte compatible. A pending image is retained across upload/start errors and cleared only after successful turn start.
- The image button is a direct visual sibling of `/`, not a toolbar or redesign. Pause, draft, keyboard send, menu, and auto-scroll behavior remain covered.
- `chat-file` parsing preserves raw-path security and same-origin behavior; ticket-file and external-link preview behavior is unchanged.
- Ticket and Chief browser tests prove non-zero rendered image geometry immediately and after navigation/reload; invalid selection produces one calm error and no duplicate turn.
- Generated `web/dist` comes from the final settled source.
