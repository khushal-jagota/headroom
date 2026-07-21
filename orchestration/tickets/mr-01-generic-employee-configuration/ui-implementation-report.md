# MR-01 Kickoff UI implementation report

## Result

The Ticket's Employee launch setup now appears only inside the Kickoff stage, immediately before
its approval content. The Ticket header no longer shows Worker. After the server freezes setup,
all Worker/Model/Reasoning controls disappear and the stored launch values are not presented as
current Employee state.

The setup uses one restrained row of the existing pill/select language:

- Worker comes only from the served Worker-type manifest backend list.
- Model comes only from `GET /api/employee-configuration-catalog`.
- Reasoning appears only when the selected backend/model catalog supports it.
- The native Model and Reasoning selections map to null launch values.
- Every edit sends the exact full `employee_backend`, `employee_launch_model`, and
  `employee_launch_reasoning_effort` snapshot to the canonical Ticket PUT endpoint and adopts the
  server-normalized Ticket response.
- Catalog requests use `candidate_model`, an AbortController, and a monotonically increasing
  generation. Old responses cannot replace the latest Worker/model catalog.
- Catalog loading is local and quiet. Failure retains the saved launch values and offers Retry;
  no frontend inventory or fallback options are invented.
- `AcpConversation.deferInitialAttach` now uses the server's
  `employee_configuration_editable` value instead of reconstructing the freeze predicate in the
  browser.

## Files

- Added `web/src/components/EmployeeConfigurationSetup.svelte`.
- Added a `beforeApproval` snippet seam to
  `web/src/components/TicketStageSection.svelte`.
- Updated `web/src/routes/TicketRoute.svelte` for placement, mutation, header removal, and the
  server-owned editable boundary.
- Updated `web/src/lib/types.ts` and `web/src/lib/lifecycle.ts` for the served contracts.
- Added small token-based layout and focus rules to `assets/app.css`; no new card or visual system
  was introduced. The transparent selects use the existing accent outline tokens for a visible
  `focus-within` keyboard cue.
- Updated `web/tests/acp-components.test.mjs` and added
  `web/tests/employee-configuration-setup.test.mjs`; both are registered in the canonical
  `web/package.json` test command.

## Focused evidence

All commands ran from `web/` unless shown otherwise.

```text
$ npm run check
svelte-check found 0 errors and 0 warnings

$ node tests/employee-configuration-setup.test.mjs
employee-configuration-setup.test.mjs: all assertions passed

$ node tests/acp-components.test.mjs
acp-components.test.mjs: all assertions passed

$ npm test
resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
ws-connection.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
acp-images.test.mjs: all assertions passed
acp-contracts.test.mjs: all assertions passed
acp-browser-state.test.mjs: all assertions passed
acp-browser-conformance.test.mjs: all assertions passed
acp-browser-components.test.mjs: all assertions passed
acp-components.test.mjs: all assertions passed
employee-configuration-setup.test.mjs: all assertions passed
acp-production-mount.test.mjs: all assertions passed

$ npm run build
182 modules transformed
production build completed successfully
```

The focused runtime component test proves initial loading, no `candidate_model` for native model,
full snapshot writes, backend dependency clearing, aborted/stale request suppression, local failure
with retained saved values, Retry, candidate-model query naming, conditional Reasoning, and complete
control removal on freeze. Its registered browser host loads the production `tokens.css` and
`app.css`, focuses the transparent Worker select, and proves the containing pill receives a visible
solid, nonzero, nontransparent outline.
