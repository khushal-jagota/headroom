# Goals and shared document readings

This area owns the seven Goal routes, saved Goal facts, completion rules, rich page validation, and dated chart projections. It supplies the same document rules and populated readings to Manager and Chief pages. Measure definitions, retained observations, manual receipts, and formula evaluation remain in `server/measures/`. Retained dataset reads remain in `server/datasets/`. Source execution remains with Unit 13.

`actions.ts` owns Goal creation, edits, promotion, and deletion through the shared write door. Promotion uses the existing Manager writer. Deletion transfers source responsibility through the narrow shared helper, quarantines private files, restores them after rollback, and purges them after commit. Shared measurement evidence remains intact.

`views.ts` separates saved Goal facts from dated readings. It combines scalar values, formula expectations, category projections, and source health within one read snapshot. `category_reads.ts` supplies complete retained inputs to pure category rules. Captured dataset declarations and timezones determine historical meaning. Empty results, absent dates, unavailable values, partial coverage, and latest membership retain distinct outcomes. Shared budgets limit chart dates and demanded measure/date pairs.

`logic/documents.ts` extends the existing Manager page preparer with chart validation and canonical dependency collectors. `manager_documents.ts` composes the four Manager seams: held Goals, rich preparation, binding validation, and document readings. `resource_versions.ts` keeps saved Goal revisions separate from measurement revisions and adds dependency-local calendar dates to readings.

Authored documents retain `JsonNumber` metadata through the shared numeric JSON helpers. Validation and arithmetic use numeric projections. Storage uses native JSON and preserves exact integer digits. Deadline and reference-date checks share `calendar.ts`. The accepted depth policy is in the [measure README](../measures/README.md) and the [deviation record](../DEVIATIONS.md).

Completion requires retained, dated evidence. `rule_evaluation.ts` finds the first proven date. `outcomes.ts` reconciles persisted completion facts without replacing them after a failed evaluation. `scheduling.ts` supplies `GoalCompletionParticipant` without its own timer or writer. `measure_sources/scheduling.ts` attaches this participant to the shared collection clock.

## Proof locations

- `logic/categories.test.ts` ports the thirteen category rule groups. `logic/completion.test.ts` ports the three selected-checklist groups.

Proof files describe executable checks, not a claim that a particular revision passed. Ticket evidence records results, execution costs, and remaining integration limits.
