# ACP-10 final audit and verification — implementation plan

## 1. Close the entry gate

Read the settled ACP-05 through ACP-08 reports/reviews and current tree. Build a short gate table with
artifact path, final verdict, focused proof, and remaining work. Stop if legacy deletion, generic
selection, real Hermes proof, or required Codex/Claude activation and real worker proof is incomplete.
The exact Codex 1.1.4 stored-plan presentation limitation is accepted; do not turn it into a parser,
private-state shim, or registration waiver.

List all active agents/processes that could edit the repository. Finish or stop them before the final
review snapshot; no implementation may overlap the canonical run.

## 2. Build the proof ledger from current evidence

Create `requirement-audit.md` using the contract's four verdicts. Walk the owner brief and program in
order, then each frozen ACP contract. Link every row to current source, a named test/evidence artifact,
and real runtime evidence where the requirement is experiential. Inspect the cited code/test rather
than copying prior report conclusions. Any missing broad proof becomes work, not a softened verdict.

Spot-check these load-bearing paths directly:

- SDK observer -> reserved ordered ingress -> one consumer -> typed hub/browser replay;
- binding generation/session CAS and Ticket mirror/backend equality;
- turn actor cancellation cause, Send Now successor, FIFO single delivery, and requested-cancel
  replacement;
- exact permission request/options/first settlement and reverse-service confinement;
- Hermes compaction fork/private-load/CAS/rekey and Codex/Claude same-session lifecycle observation,
  each under the one 300-second breaker with content-free public started/finished/exact-failure state;
- schema-25 transaction/rollback and schema-26 Ticket rebuild/parser ordering;
- configured backend catalog/Worker registry pair and human/automatic `ConversationEmployee` reuse;
- `EmployeeStepRunner`/repository/pending-worker-context/`AcpStepGateway` settlement boundary.

## 3. Prove qualification and deletion truth

Write `backend-qualification.md`. Reinspect locked manifests, initialized identities/capabilities,
registration tuple, served catalog, focused conformance evidence, and real dogfood records. Do not
change packages merely to make the audit pass. Exact Codex 1.1.4 and Claude 0.60.0 must both be present
only after their frozen definitions, lifecycle behavior, and real human/automatic continuity pass;
Claude additionally requires its initialize-only startup preflight before admission.

Run the ACP-06 closure searches and schema/route inspections after the final production build. Save
commands, exit codes, complete matches, explicit exclusions, and the sealed historical recognizer in
`legacy-absence-evidence.md`. A surprising match is classified and removed or retained with a named
contract reason; it is never filtered from output to make the search green.

Audit the affected live docs and root architecture maps. Correct stale current-state prose now. Draft
the final `PROGRESS.md`/`decisions.md` entries, leaving only the canonical verify result pending.

## 4. Run the real `8767` experience

Gracefully restart the production `panels serve` process from the settled tree and hard reload real
Chrome. Use ordinary UI creation/actions for the contract's Hermes, Codex, and Claude matrix. Reuse actions
where one sequence proves several outcomes, but record each matrix row separately with before/after
session IDs and visible receipts/statuses. Wait normally for compaction; only Panels' five-minute
timer is a deadline failure.

Use disposable Tickets and fresh conversations where old state would make the result ambiguous.
Remove only those disposable artifacts through ordinary product cleanup after evidence is captured.
Do not clear unrelated user data. Record deterministic automated evidence separately for automatic
compaction or disconnect races that cannot honestly be induced; do not count it as a screenshot.

Write `computer-use-evidence.md` immediately while session/binding details are available. Every
experiential requirement must say `PASS` with exact observation or remain `UNPROVED`.

## 5. Resolve the audit, then commission one review

Resolve every ledger/search/Computer Use gap in its owning ticket with only focused checks. Any source,
test, doc, dependency, or generated-asset edit invalidates earlier final-audit observations that
depended on it; refresh those rows before review. Do not run `./verify`.

When the ledger has no `unproved` row, dispatch one independent sub-agent with the owner brief,
program, frozen contracts, current diff/tree, all ACP-10 evidence, and live docs. Ask for concrete
requirement violations only and full finding provenance. Persist its output as
`independent-review.md`; answer every point in `review-disposition.md`. If a material fix is required,
return to the owning ticket and refresh the invalidated evidence before proceeding. Use a narrow
finding check only when the original reviewer cannot otherwise close a concrete corrected blocker;
never repeat the broad audit ceremonially.

## 6. Freeze and run the single canonical gate

After a no-unresolved-violation review:

1. finish/stop every repository writer and gracefully stop the live server;
2. confirm no active Employee turn or package/build process can mutate verification inputs;
3. save `git status --porcelain` and a sorted SHA-256 manifest of all tracked and non-ignored
   verification inputs, excluding `.git`, dependency/cache directories, `data/**`, and only the
   ACP-10 evidence/memory files that receive the result afterward;
4. record cwd, Python/Node versions, UTC start time, and exact command;
5. run once with pipe failure preserved:

```bash
mkdir -p data/verify
set -o pipefail
./verify 2>&1 | tee data/verify/acp-10-final.log
```

Do not rerun a passing command to quote it. Require exit status zero and the complete final marker
`VERIFY: PASS`; compute `shasum -a 256 data/verify/acp-10-final.log`. Save the status, digest, byte/
line count, start/end time, and every reported suite count in `verification-report.md`. Keep the full
log at that exact path. Regenerate the input manifest and require byte-for-byte equality; verification
may write ignored runtime artifacts but may not change the verified source, tests, docs, config, or
served bundle.

If the run fails, retain its full output and report the failure. Do not call it canonical success,
silently patch the tree, or immediately run a second full gate. Diagnose with focused checks and
return to the audit/freeze decision explicitly.

## 7. Close memory without changing the proved tree

After the clean run, update only `verification-report.md`, the pending exact result/digest lines in
`PROGRESS.md` and `decisions.md`, and the final requirement-ledger verification row. Reconfirm the
verified-input manifest. The completion report names:

- zero unproved requirements and zero unresolved review findings;
- exact registered backend keys `hermes, codex, claude` and their qualification evidence;
- real Computer Use matrix result on `127.0.0.1:8767`;
- legacy absence proof and current-doc result; and
- the sole canonical log path, SHA-256 digest, exit status, and `VERIFY: PASS` marker.

Only then may ACP-10 and the full ACP migration be marked complete.
