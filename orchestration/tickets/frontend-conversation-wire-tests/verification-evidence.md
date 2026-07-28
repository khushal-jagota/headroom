# Conversation wire test decomposition verification evidence

These are the commands run against the settled implementation tree and their
full, unabridged output. Every command exited with status 0.

## Directly affected suites after review fixes

```sh
npm --prefix web run test:vitest -- tests/conversation-feed.test.ts tests/conversation-transcript.test.ts tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts
```

```text
> test:vitest
> vitest run --typecheck tests/conversation-feed.test.ts tests/conversation-transcript.test.ts tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-wire-tests/web


 Test Files  10 passed (10)
      Tests  86 passed (86)
Type Errors  no errors
   Start at  18:58:17
   Duration  558ms (transform 258ms, setup 0ms, import 316ms, tests 22ms, environment 0ms, typecheck 408ms)
```

## Twelve named replacement suites

```sh
npm --prefix web run test:vitest -- tests/conversation-pending-images.test.ts tests/conversation-feed.test.ts tests/conversation-transcript.test.ts tests/conversation-composer-asks.test.ts tests/conversation-composer-delivery.test.ts tests/conversation-outgoing.test.ts tests/conversation-outgoing-storage.test.ts tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts tests/conversation-tool-presentation.test.ts tests/conversation-wire-values.test.ts
```

```text
> test:vitest
> vitest run --typecheck tests/conversation-pending-images.test.ts tests/conversation-feed.test.ts tests/conversation-transcript.test.ts tests/conversation-composer-asks.test.ts tests/conversation-composer-delivery.test.ts tests/conversation-outgoing.test.ts tests/conversation-outgoing-storage.test.ts tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts tests/conversation-tool-presentation.test.ts tests/conversation-wire-values.test.ts

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-wire-tests/web


 Test Files  24 passed (24)
      Tests  214 passed (214)
Type Errors  no errors
   Start at  18:58:23
   Duration  632ms (transform 738ms, setup 0ms, import 1.03s, tests 265ms, environment 1ms, typecheck 292ms)
```

## Svelte and TypeScript check

```sh
npm --prefix web run check
```

```text
> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-wire-tests/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

## Production build

```sh
npm --prefix web run build
```

```text
> build
> vite build

vite v6.4.3 building for production...

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 547 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                                0.78 kB │ gzip:   0.40 kB
dist/assets/newsreader-vietnamese-400-normal-DdKr49mV.woff2    5.43 kB
dist/assets/newsreader-vietnamese-500-normal-CL6a8tp2.woff2    5.44 kB
dist/assets/newsreader-vietnamese-600-normal-CaH84vfx.woff2    5.50 kB
dist/assets/newsreader-vietnamese-400-normal-BekUZro8.woff     6.97 kB
dist/assets/newsreader-vietnamese-500-normal-BEAbKU8A.woff     7.04 kB
dist/assets/newsreader-vietnamese-600-normal-CVAR0otO.woff     7.06 kB
dist/assets/newsreader-latin-ext-400-normal-svq1FPys.woff2    14.21 kB
dist/assets/newsreader-latin-ext-500-normal-BNHmvKvI.woff2    15.07 kB
dist/assets/newsreader-latin-ext-600-normal-BXv5iMHi.woff2    15.17 kB
dist/assets/newsreader-latin-ext-400-normal-DYA1XoQK.woff     18.38 kB
dist/assets/newsreader-latin-ext-500-normal-CZruMFou.woff     19.30 kB
dist/assets/newsreader-latin-ext-600-normal-BrbfzHZ5.woff     19.39 kB
dist/assets/newsreader-latin-400-normal-BFBkh4jY.woff2        22.48 kB
dist/assets/newsreader-latin-500-normal-B66TYsaK.woff2        23.62 kB
dist/assets/newsreader-latin-600-normal-30OJ_TG_.woff2        23.88 kB
dist/assets/newsreader-latin-400-normal-gRTjlS2D.woff         28.29 kB
dist/assets/newsreader-latin-500-normal-DFwuUcdu.woff         29.64 kB
dist/assets/newsreader-latin-600-normal-DUnT2r2g.woff         29.78 kB
dist/assets/index-D_OL2IiV.css                                26.08 kB │ gzip:   4.20 kB
dist/assets/index-DTCHXG1_.js                                483.01 kB │ gzip: 152.41 kB
✓ built in 1.16s
```

## Complete web test command

```sh
npm --prefix web test
```

```text
> test
> npm run test:vitest && npm run test:legacy


> test:vitest
> vitest run --typecheck

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-wire-tests/web


 Test Files  36 passed (36)
      Tests  354 passed (354)
Type Errors  no errors
   Start at  18:58:39
   Duration  777ms (transform 1.20s, setup 0ms, import 1.27s, tests 509ms, environment 1ms, typecheck 290ms)


> test:legacy
> node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/browser-css.test.mjs && node tests/vps-status.test.mjs && node tests/backlog-ideas.test.mjs && node tests/production-surfaces.test.mjs && node tests/worker-configuration-setup.test.mjs && node tests/conversation-rest-line.test.mjs && node tests/conversation-pane.test.mjs

browser-css.test.mjs: all assertions passed
vps-status.test.mjs: all assertions passed
backlog-ideas.test.mjs: all assertions passed
production-surfaces.test.mjs: all assertions passed
worker-configuration-setup.test.mjs: all assertions passed
conversation-rest-line.test.mjs: all assertions passed
conversation-pane.test.mjs: all assertions passed
```

## Whitespace check

```sh
git diff --check
```

The command produced no output and exited with status 0.

## Final scope, size, and replacement audit

```sh
find web/tests -maxdepth 1 -name 'conversation-*.test.ts' -print0 | sort -z | xargs -0 wc -l
wc -l web/tests/support/conversationEvents.ts
if find web/tests -maxdepth 1 -name 'conversation-*.test.ts' -print0 | xargs -0 wc -l | awk '$2 != "total" && $1 > 350 { found=1 } END { exit !found }'; then
  echo 'Found a conversation Vitest suite above 350 lines.'
else
  echo 'All conversation Vitest suites are at or below 350 lines.'
fi
if [ -e web/tests/conversation-wire.test.mjs ]; then
  echo 'web/tests/conversation-wire.test.mjs still exists.'
else
  echo 'web/tests/conversation-wire.test.mjs is absent.'
fi
harness_hits=$(rg -n 'createHarness|setupHarness|ConversationHarness' web/tests/conversation-*.test.ts web/tests/support/conversationEvents.ts || true)
if [ -n "$harness_hits" ]; then
  echo "$harness_hits"
else
  echo 'No generic harness helper references found.'
fi
disallowed_hits=$(rg -n 'LIVE_COUNTER|TOOL_PRESENTATION|resetConversation|vi\.mock\(' web/tests/conversation-*.test.ts web/tests/support/conversationEvents.ts || true)
if [ -n "$disallowed_hits" ]; then
  echo "$disallowed_hits"
else
  echo 'No disallowed test-owned policy constants, production reset helpers, or module mocks found.'
fi
positional_turn_end_hits=$(rg -n 'turnEndedEvent\([^,]+,\s*("|null)' web/tests/conversation-*.test.ts || true)
if [ -n "$positional_turn_end_hits" ]; then
  echo "$positional_turn_end_hits"
else
  echo 'No positional turn-ended payload arguments found.'
fi
rg -n 'VISIBLE_RUNNING_WORK_ENTRIES|ConversationEventMetadata|TurnEndedOptions|senderMessageRecordEvent|THREE_MIB_IN_BYTES' web/tests/conversation-*.test.ts web/tests/support/conversationEvents.ts
git status --short
```

```text
     133 web/tests/conversation-composer-asks.test.ts
     177 web/tests/conversation-composer-delivery.test.ts
     269 web/tests/conversation-feed.test.ts
     187 web/tests/conversation-outgoing-storage.test.ts
     146 web/tests/conversation-outgoing.test.ts
     223 web/tests/conversation-pending-images.test.ts
     301 web/tests/conversation-thread-items.test.ts
     104 web/tests/conversation-thread-plan.test.ts
     185 web/tests/conversation-tool-presentation.test.ts
     278 web/tests/conversation-transcript.test.ts
     165 web/tests/conversation-turn-time.test.ts
      52 web/tests/conversation-wire-values.test.ts
    2220 total
     144 web/tests/support/conversationEvents.ts
All conversation Vitest suites are at or below 350 lines.
web/tests/conversation-wire.test.mjs is absent.
No generic harness helper references found.
No disallowed test-owned policy constants, production reset helpers, or module mocks found.
No positional turn-ended payload arguments found.
web/tests/conversation-thread-items.test.ts:14:  VISIBLE_RUNNING_WORK_ENTRIES
web/tests/conversation-thread-items.test.ts:88:    expect(VISIBLE_RUNNING_WORK_ENTRIES).toBe(1);
web/tests/conversation-thread-items.test.ts:91:        .slice(-VISIBLE_RUNNING_WORK_ENTRIES)
web/tests/support/conversationEvents.ts:14:type ConversationEventMetadata = {
web/tests/support/conversationEvents.ts:18:type PromptOverrides = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:22:type AgentOverrides = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:25:type TurnEndedOptions = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:29:type ToolCallStartedOptions = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:35:type ToolCallFinishedOptions = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:40:type PermissionAskedOptions = ConversationEventMetadata & {
web/tests/support/conversationEvents.ts:48:function eventMetadata(sequence: number, metadata: ConversationEventMetadata) {
web/tests/support/conversationEvents.ts:92:  }: TurnEndedOptions = {}
web/tests/support/conversationEvents.ts:126:  overrides: ConversationEventMetadata = {}
web/tests/conversation-pending-images.test.ts:10:const THREE_MIB_IN_BYTES = 3 * 1024 * 1024;
web/tests/conversation-pending-images.test.ts:135:      new File([new Uint8Array(THREE_MIB_IN_BYTES + 1)], "huge.png", { type: "image/png" })
web/tests/conversation-pending-images.test.ts:162:      [new Uint8Array(THREE_MIB_IN_BYTES)],
web/tests/conversation-pending-images.test.ts:175:    expect(intake.accepted[0].byteCount).toBe(THREE_MIB_IN_BYTES);
web/tests/conversation-pending-images.test.ts:181:      [new Uint8Array(THREE_MIB_IN_BYTES / 2)],
web/tests/conversation-pending-images.test.ts:186:      [new Uint8Array(THREE_MIB_IN_BYTES / 2 + 1)],
web/tests/conversation-pending-images.test.ts:215:      THREE_MIB_IN_BYTES - 1
web/tests/conversation-outgoing.test.ts:23:function senderMessageRecordEvent(
web/tests/conversation-outgoing.test.ts:113:        [senderMessageRecordEvent(1, kind, first)]
 M web/package.json
 D web/tests/conversation-wire.test.mjs
?? orchestration/tickets/frontend-conversation-wire-tests/
?? web/tests/conversation-composer-asks.test.ts
?? web/tests/conversation-composer-delivery.test.ts
?? web/tests/conversation-feed.test.ts
?? web/tests/conversation-outgoing-storage.test.ts
?? web/tests/conversation-outgoing.test.ts
?? web/tests/conversation-pending-images.test.ts
?? web/tests/conversation-thread-items.test.ts
?? web/tests/conversation-thread-plan.test.ts
?? web/tests/conversation-tool-presentation.test.ts
?? web/tests/conversation-transcript.test.ts
?? web/tests/conversation-turn-time.test.ts
?? web/tests/conversation-wire-values.test.ts
?? web/tests/support/
```
