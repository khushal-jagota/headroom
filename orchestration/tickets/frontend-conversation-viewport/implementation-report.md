# Implementation report

## Outcome

The conversation pane is now a composition shell rather than the owner of the thread's
rendering, DOM geometry, and scrolling policy.

- `ConversationPane.svelte` owns the application-facing props, header/menu, layer-state
  transitions, rest line, composer, and pane-level visibility rules.
- `viewport/ConversationViewport.svelte` owns the complete thread shell, transcript,
  optimistic messages, reserved answer room, Latest control, reader intent, scroll
  policy, and all four scheduling sites.
- `viewport/threadGeometry.ts` is the exact private read-only geometry boundary. It owns
  DOM traversal, selectors, rectangles, and coordinate arithmetic, but performs no DOM
  or Svelte-state mutation.
- `viewport/viewportConfiguration.ts` contains all five tunable viewport values.

The pane renders one viewport and passes exactly the nine locked facts. The viewport has
no bindable prop, event dispatcher, custom output, route/live-conversation dependency,
composer dependency, or rest-bar dependency.

## Behavior preservation

The move retains:

- the existing thread-shell DOM order, selectors, roles, accessible names, outgoing
  message keying, empty-state condition, discard condition, and styles;
- cached newest-line geometry on the scroll hot path, with no content traversal or
  rectangle read from `newestLineIsInSight`;
- forward-only automatic following and explicit backward movement only through Latest;
- sent-message anchoring, canonical prompt fallback, answer-room growth/release, and
  stale-render request invalidation;
- the source order of rows/outgoing pre-effect, generic size effect, conversation-state
  pre-effect, and fold-click handler;
- the original `tick` and `untrack` boundaries, including keeping reserved-space
  geometry untracked in the rows effect;
- held-line restoration for row/fold/layer-state changes and the existing weaker generic
  resize/media behavior: remeasure, follow only when already following, and no deliberate
  correction for a reader who is away.

## Harness correction

The first structural gate exposed that the legacy harness inspected only top-level
conversation component sources. After owner review, the ticket and plan were corrected.
The exact top-level inventory assertion remains unchanged, while the nested
`viewport/ConversationViewport.svelte` source is additionally compiled and checked by
the existing warnings, design-token, and shared-class loop. No rendered or browser
behavior assertion changed.

## Contract audit

Settled line counts:

```text
320 web/src/components/conversation/ConversationPane.svelte
438 web/src/components/conversation/viewport/ConversationViewport.svelte
163 web/src/components/conversation/viewport/threadGeometry.ts
 14 web/src/components/conversation/viewport/viewportConfiguration.ts
```

The pane deletion-list search returned no matches. Import/search audits show:

- only `ConversationPane.svelte` imports `ConversationViewport`;
- only `ConversationViewport.svelte` imports `threadGeometry`;
- only the viewport folder imports `viewportConfiguration`;
- no viewport file imports pane, composer, rest bar, routes, HTTP clients, or
  live-conversation orchestration;
- no viewport file contains `$bindable`, `createEventDispatcher`, or `dispatch(...)`.

The rebuilt bundle replaces:

```text
web/dist/assets/index-BLYhu5uk.js
web/dist/assets/index-D_OL2IiV.css
```

with:

```text
web/dist/assets/index-CAMyhuZ7.js
web/dist/assets/index-C8ABQvyo.css
```

and `web/dist/index.html` points at those exact assets.

## Verification

The untouched baseline is recorded in `baseline-evidence.md`. The final settled gate
outputs are recorded in `verification-evidence.md`. Every named focused gate passed:

- structural conversation pane harness;
- Svelte/TypeScript diagnostics;
- production build;
- 38 Vitest files / 366 tests plus every legacy frontend harness;
- both targeted Playwright files;
- strict diff whitespace check outside generated JavaScript, plus confirmation that
  its sole flagged sequence is the same intentional Svelte runtime whitespace
  template literal present in the previous committed bundle.

The implementation is ready for the ticket branch commit.
