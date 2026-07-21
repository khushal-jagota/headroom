# ACP-05 permission diff implementation report

## Result

`PermissionPrompt.svelte` now filters the pending permission's exact `toolCall.content` to its typed
`diff` entries and renders those entries, in source order, through the existing `DiffView.svelte`
between the unchanged title and permission options. It adds no styles, actions, raw-content display,
or diff logic.

The mounted browser proof covers:

- no content matching the exact serialized pre-change compact permission markup, normalizing only
  Svelte's compiler-generated scope token;
- only non-diff content remaining byte-identical to that no-content markup;
- arbitrary `content` and `terminal` entries remaining hidden;
- two diff entries retaining source order;
- old/new lines, old/new line numbers, and accessible deleted/added markers;
- unchanged option labels, ACP kinds, callback request/option IDs, submitting state, and disabled state.

## Files changed

- `web/src/components/acp/PermissionPrompt.svelte`
- `web/tests/acp-browser-components.test.mjs`
- `orchestration/tickets/acp-05-permission-diff/implementation-plan.md`
- `orchestration/tickets/acp-05-permission-diff/implementation-report.md`

## TDD evidence

The mounted component test was added before production code. Its first run failed at the new public
browser assertion because zero permission diffs were rendered:

```text
AssertionError: assert diffs.count() == 2
```

After the minimal component implementation and correction of one test-only exact-name assertion, the
settled focused run was:

```text
$ node web/tests/acp-browser-components.test.mjs
acp-browser-components.test.mjs: all assertions passed
```

## Independent-review correction

The independent review correctly found that comparing two states produced by the changed component
did not prove equality with the pre-change markup. It also identified the empty `<!---->` anchor
compiled for an empty `{#each}` block.

The browser test now contains the full serialized pre-change permission inner markup as its expected
fixture. The assertion normalizes only Svelte's generated `svelte-*` CSS scope token; every element,
text node, attribute, order, and comment node remains exact. Before the production correction, this
new assertion failed and showed the extra anchor:

```text
AssertionError: '<div ...>Approve runtime tool</div> <!----> <div ...'
```

`PermissionPrompt.svelte` no longer contains a template diff loop. Its reactive effect mounts the
existing `DiffView` instances before the existing options element only when at least one filtered diff
exists, and unmounts them on replacement or teardown. With zero diffs it creates no node or anchor, so
the exact pre-change fixture passes; the raw no-content/non-diff comparison remains as a second proof.

## Diagnostics and build

```text
$ cd web && npm run check
> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/.hermes/planning-v2/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

```text
$ cd web && npm run build
> build
> vite build

vite v6.4.3 building for production...
<script src="/assets/markdown.js"> in "/index.html" can't be bundled without type="module" attribute
/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 182 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                                0.75 kB │ gzip:  0.38 kB
dist/assets/index-B15RFj9L.css                                 9.65 kB │ gzip:  1.90 kB
dist/assets/index-DpMYEmDB.js                                212.26 kB │ gzip: 70.16 kB
✓ built in 865ms
```

`./verify` was not run, as reserved by the root migration gate.
