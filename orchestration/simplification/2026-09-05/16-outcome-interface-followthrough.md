# Outcome interface follow-through

Root spot-check of O5 commit `20e523c3`, before program integration. This is an
implementation correction and narrow scope expansion; the independent combined review
still remains due. No conversation component or interaction contract changes.

1. The shared Outcome picker stores a returned creation ID but its Create button always
   issues another POST. If the subsequent commitment fails, retry must call the choose
   action with the retained Outcome. Show that concrete retry control, prevent a second
   creation while that result is pending, and preserve the entered brief. The original
   two-step contract already requires this behavior. Do not introduce a generic new
   idempotency service. An uncertain first creation response must not trigger automatic
   creation retries; keep the form and make recovery through the catalog possible.
2. Each committed-Sprint link currently goes to the current Sprint. Add one explicit
   query parameter, `#/sprint?sprint=<id>`, with matching documents/Item links as needed.
   App passes optional `sprintId` to SprintRoute. The route chooses the existing explicit
   tracking endpoint for that ID and the current endpoint when omitted. Back links and
   document edits use the selected Sprint. Existing current-Sprint addresses keep their
   behavior. Root explicitly extends allowed scope to the narrow routing/props region of
   `web/src/App.svelte`; no unrelated app-shell or conversation changes.
3. Keep the compact Outcome row as the default Sprint experience. Carry/Remove belong in
   a collapsed ordinary action disclosure/menu, and child Tickets stay collapsed until
   requested. The source-Sprint children remain the complete truthful query partition;
   expansion reveals each once. Do not turn ten brief rows into a long wall of all their
   historical/current Ticket text and repeated action buttons. Native details/disclosure
   is sufficient; no generic menu framework or new sidebar surface.
4. Prove the named UI behavior, rather than only re-running old presentation helpers.
   Use the existing local frontend browser/component harness with mocked HTTP: failed
   commitment then retry uses one POST and the returned ID; carry begins unchecked and
   sends exactly selected unfinished IDs; Backlog catalog query mounts only expanded;
   clicking a historical commitment loads that explicit Sprint and keeps its documents
   and back links there. Combine related steps into coherent user journeys, not source
   text assertions or a new live-server E2E matrix. Existing behavioral tests may be
   replaced where these journeys own their risk. Counts still include every new case.
