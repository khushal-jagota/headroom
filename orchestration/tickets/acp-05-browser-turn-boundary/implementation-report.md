# ACP-05 browser turn-boundary implementation report

## Outcome

Implemented the frozen browser-only correction.

- Terminal `idle`, `interrupted`, and `failed` activities now close only the active missing-ID agent
  group.
- An `interrupted` delivery receipt closes the same group for Send Now handoff.
- The helper preserves the turn ordinal, current role, and segment ordinal.
- All other activity and receipt states keep the group open.
- No backend, component, generated distribution, config, runtime database, Hermes checkout,
  `PROGRESS.md`, or `decisions.md` file was changed by this ticket.

## TDD evidence

The queued-turn regression failed before the production change:

```text
$ cd web && node tests/acp-browser-state.test.mjs
node:internal/modules/run_main:123
    triggerUncaughtException(
    ^

AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:

1 !== 2

    at runStateAssertions (file:///Users/khushaljagota/.hermes/planning-v2/web/tests/acp-browser-state.test.mjs:232:10)
    at file:///Users/khushaljagota/.hermes/planning-v2/web/tests/acp-browser-state.test.mjs:70:3 {
  generatedMessage: true,
  code: 'ERR_ASSERTION',
  actual: 1,
  expected: 2,
  operator: 'strictEqual',
  diff: 'simple'
}

Node.js v22.22.3
```

After the minimal reducer change:

```text
$ cd web && node tests/acp-browser-state.test.mjs
acp-browser-state.test.mjs: all assertions passed
```

The focused test now covers:

1. The observed queue ordering: optimistic prompt and matching human echo before the predecessor's
   final missing-ID chunk, terminal idle, successor start, then the successor's missing-ID chunk.
   It asserts two distinct agent IDs and the exact separate texts `BASE TURN COMPLETE` and
   `QUEUED MESSAGE DELIVERED.`
2. An interrupted Send Now receipt between two missing-ID chunks, asserting two exact agent
   messages.
3. Thinking, working, steer acceptance, queued, and started non-terminal updates between two chunks,
   asserting one continuous agent response.

## Focused verification

```text
$ cd web && node tests/acp-browser-state.test.mjs && node tests/acp-browser-components.test.mjs && npm run check
acp-browser-state.test.mjs: all assertions passed
acp-browser-components.test.mjs: all assertions passed

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/.hermes/planning-v2/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

The production build was directed to a fresh temporary directory so this ticket did not write
`web/dist`:

```text
$ cd web && npm run build -- --outDir /tmp/panels-acp05-turn-boundary-build.eibyPu

> build
> vite build --outDir /tmp/panels-acp05-turn-boundary-build.eibyPu

vite v6.4.3 building for production...
<script src="/assets/markdown.js"> in "/index.html" can't be bundled without type="module" attribute

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 182 modules transformed.
rendering chunks...
computing gzip size...
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/index.html                                                0.75 kB │ gzip:  0.38 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-400-normal-DdKr49mV.woff2    5.43 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-500-normal-CL6a8tp2.woff2    5.44 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-600-normal-CaH84vfx.woff2    5.50 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-400-normal-BekUZro8.woff     6.97 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-500-normal-BEAbKU8A.woff     7.04 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-vietnamese-600-normal-CVAR0otO.woff     7.06 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-400-normal-svq1FPys.woff2    14.21 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-500-normal-BNHmvKvI.woff2    15.07 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-600-normal-BXv5iMHi.woff2    15.17 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-400-normal-DYA1XoQK.woff     18.38 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-500-normal-CZruMFou.woff     19.30 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-ext-600-normal-BrbfzHZ5.woff     19.39 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-400-normal-BFBkh4jY.woff2        22.48 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-500-normal-B66TYsaK.woff2        23.62 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-600-normal-30OJ_TG_.woff2        23.88 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-400-normal-gRTjlS2D.woff         28.29 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-500-normal-DFwuUcdu.woff         29.64 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/newsreader-latin-600-normal-DUnT2r2g.woff         29.78 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/index-4efN4D-d.css                                 9.65 kB │ gzip:  1.89 kB
../../../../../tmp/panels-acp05-turn-boundary-build.eibyPu/assets/index-DkrGBzku.js                                211.90 kB │ gzip: 70.00 kB
✓ built in 970ms
```

No canonical `./verify` run was performed; ACP-10 retains that run.
