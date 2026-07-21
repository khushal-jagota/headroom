# MR-01 Kickoff UI implementation review

## Verdict

**READY after narrow correction checks**

The product flow is otherwise aligned with the frozen contract: the setup is inside Kickoff before
the approval content, the header has no Worker control, every edit sends a complete snapshot and
adopts the returned normalized Ticket, Reasoning follows the selected catalog, stale catalog replies
cannot win, and the server-provided editable flag removes both the controls and initial ACP attach
deferment together. The visual treatment is restrained and reuses Panels' existing pill language.

## Findings

### 1. P1 — the new browser proof is outside the canonical verification gate

`web/package.json:9` does not run either the newly added
`employee-configuration-setup.test.mjs` or the modified `acp-components.test.mjs`. `./verify` invokes
only `npm --prefix web test`, so the required loading, Retry, stale-response, conditional Reasoning,
complete-snapshot, and freeze assertions can all regress while the canonical gate remains green.

Add both tests to the `test` script (or move their assertions into an already registered suite), then
run the registered frontend gate. Directly running both files is useful focused evidence but is not a
substitute for making them part of `./verify`.

### 2. P2 — the three transparent selects have no visible keyboard focus

`EmployeeConfigurationSetup.svelte:220-253` places Worker, Model, and Reasoning in `.pill` elements.
The actual focusable controls are fully transparent at `assets/app.css:2521-2526`, while `.pill` has
only a hover treatment and no `:focus-within` treatment. A keyboard user can tab to and operate the
controls, but cannot see which control has focus.

Add a restrained `.employee-configuration-setup .pill:focus-within` treatment using existing color,
border, or outline tokens, and include a focused browser assertion that the visible pill changes when
its select receives focus.

## Focused evidence

- `npm --prefix web run check`: 0 errors and 0 warnings.
- `node web/tests/employee-configuration-setup.test.mjs`: passed.
- `node web/tests/acp-components.test.mjs`: passed.
- Scoped `git diff --check`: passed.

No backend files were reviewed and no product code was changed.

## Narrow correction check

Verdict at this check: **NOT READY**

### Finding 1 — RESOLVED

`web/package.json:9` now registers both `acp-components.test.mjs` and
`employee-configuration-setup.test.mjs` in the exact `npm test` command used by `./verify`. A fresh
`npm --prefix web test` ran the complete registered sequence, visibly executed both files, and
passed.

### Finding 2 — PARTIALLY RESOLVED

`assets/app.css:2220-2223` now gives the visible Employee-configuration pill an accent outline through
`:focus-within`, so the transparent select's keyboard focus has a visible product treatment using
existing tokens. However, the requested browser regression was not added:
`web/tests/employee-configuration-setup.test.mjs` contains no focus action or visible-style
assertion. Its isolated host also does not import `assets/app.css`, so its current passing run cannot
prove this correction.

Add the bounded focus assertion to a registered browser test that loads the production stylesheet.
No broader correction or review is required.

## Final narrow correction check

Verdict: **READY**

The sole remaining finding is resolved. The registered
`employee-configuration-setup.test.mjs` harness now imports the production `assets/app.css`, focuses
the transparent Worker select, reads the visible parent pill's computed outline, and requires a
solid, nonzero, nontransparent result. A fresh direct run passed:

```text
employee-configuration-setup.test.mjs: all assertions passed
```

The test remains registered in `web/package.json`, so `./verify` will exercise this correction. No
unresolved UI-review finding remains.
