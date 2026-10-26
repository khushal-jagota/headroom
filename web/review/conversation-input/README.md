This review artifact compiles the current conversation components into one HTML file. It opens in Panels' opaque-origin, `allow-scripts` artifact sandbox. It sends no messages to agents.

Build from the repository root after `npm ci --prefix web`:

```sh
node web/review/conversation-input/build.mjs /home/vps/Deployments/Panels/current/data/files/tickets/t_2g0vwu4y/artifacts/clarification/candidate/preview.html
```

Without an output argument, the builder writes `web/review-dist/conversation-input.html`. A sibling `.build.json` records the commit, dirty source status, hashes for the bundled repository source inputs, compiled CSS, JavaScript, and final HTML. The HTML embeds the same provenance except its own hash. Rebuild after the final commit to associate the artifact with that revision.

The preview uses production `ConversationPane`, `ConversationComposer`, `ManagerFrame`, `ManagerHeader`, `ArtifactPreview`, `FileDocument`, the dock action, the dismissal action, and shared CSS. Ticket markup follows the existing `conversation-pane.mjs` fixture. Chief uses its production `ManagerFrame` and `chief-workspace` class. The artifact host mounts the real Ticket artifact layout.

Two review-only adapters remove external execution requirements. The Markdown worker interface calls the exact production parser within this document, for the finite synthetic samples. The nested artifact document uses inline `srcdoc` instead of live managed-file reads and navigation. Its production layout remains intact. The inline sample uses the production document preparation and registered-frame press bridge, so artifact presses reach the production dismissal action. These adapters do not demonstrate worker isolation or live artifact navigation.

The Saved position selector uses the production `ConversationViewReader` and a captured `viewAnchor`. Each selection resets the synthetic pane and its draft. Each scenario starts with its already-admitted inline target text, scrolls to an earlier native character, and captures the actual immutable text locator. Only that initial setup scroll changes the scroll position directly. A replacement reader receives the captured anchor. Its transport returns the same target as a deferred body, so response gates exercise actual restoration. New synthetic output moves the newest range beyond the retained historical window. Production admission and placement determine the final position.

The scenarios cover successful saved text with a held body, the five-second visible timeout, an unavailable target, and a genuine HTTP 503 response for newest admission. Release saved body resolves the actual pending synthetic response. In the timeout case, a late release must not restore the retired target. Fetch failures retain readable content and the existing production error indication. The synthetic controls describe scenario setup only. Reader outcomes and phases remain in hidden probe attributes. No visible saved-position status, progress text, warning, or Retry appears in the product pane, including while a real body response remains held. Ordinary loading semantics and the production connection-error indication remain.

`web/tests/support/conversationViewReaderFixture.ts` supplies bounded indexed pages, deferred bodies, and response gates. Explicit search and URL-base options permit opaque-origin delivery. Existing browser cases retain their defaults. The synthetic URL base never authorizes network access. The local send adapter publishes synthetic owner and agent rows through the fixture snapshot path.

Viewport widths are 736, 1250, and 390 pixels, capped by the available artifact frame width. To inspect exact 1250-pixel geometry, open the artifact in a frame at least that wide. The compiled UI lives in an inline sandboxed child iframe. The size selector sends a validated message to its parent wrapper, which resizes that iframe. Media queries therefore use the selected width. The displayed width records the requested width. Measure the child viewport to record the effective width.

Drafts survive conversation open/dock movement because the production composer remains mounted. Switching between Ticket and Manager layouts replaces that mount. The opaque sandbox rejects session storage, so drafts do not survive that replacement or an artifact reload. This is existing production storage behavior, not a preview persistence mechanism.

The document contains synthetic local conversations and local send responses. Files, slash commands, provider changes, live endpoints, and module-worker isolation are outside the preview's scope. No production component styling changes are part of this preview.


Run the acceptance probe against the managed sandbox:

```sh
.venv/bin/python scripts/conversation_input_review/verify_preview.py --base-url http://127.0.0.1:8767 --ticket t_2g0vwu4y --path clarification/candidate/preview.html --output /home/vps/Deployments/Panels/current/data/files/tickets/t_2g0vwu4y/artifacts/clarification/evidence
```

The probe reuses the existing deployed artifact host. It adds no permanent browser host. Its forty-eight recovery cases cross four outcomes, four hosts, and three real child viewport widths. The twelve five-second timeout cases cost at least sixty seconds. The evidence records elapsed time, native text location, scroll geometry, request activity, and browser errors. Existing frontend cases retain the deeper admission and cancellation matrix. A real-app held HTTP 200 probe remains necessary for live endpoint and worker-parser evidence.

The probe observes product mutations and animation frames from scenario setup through pending restoration, final placement, and late response release. It asserts that no restoration-specific status or text appears. Held-body cases prove a pending reader and ordinary loading semantics before release. The genuine newest-fetch failure case proves the production Connection trouble indication in the dock.
