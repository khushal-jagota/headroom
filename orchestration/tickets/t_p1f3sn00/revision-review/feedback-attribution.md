The original feedback frame jump remains unreproduced. Attribution is inconclusive. Neither a pre-existing defect nor a branch regression is established.

The original strict assertion remains unchanged: every consecutive entrance height delta must be less than8px. Original failure:10.97px.

| Build context | Baseline staging | Ticket branch |
| --- | --- | --- |
| feedback-attribution | passed, 24.021s, max 6.19px | passed, 24.023s, max 6.36px |
| feedback-attribution-shared | passed, 23.896s, max 6.21px | passed, 23.938s, max 6.21px |

Each context runs the original full feedback case exactly once per tree, baseline first. Each pair uses one Chromium149.0.7827.55 process on the same host, origin and URL. Contexts are fresh. Input, viewport and clock setup come from the original case. A Page.evaluate wrapper records the existing returned frame arrays without extra browser reads. No case, tolerance or source file changed.

The baseline uses a new isolated worktree atc1d99e049b3d8daa121c21cd0a88ac8d9234d546. Its virtualenv and both npm dependency trees came from fresh installations. The branch remains974aee1a9541241bc63625ea5fd8d15c84102329. The baseline tree remains available.

The first context builds feedback alone. The second context uses the original shared input order: feedback-inbox, workspace-utility-pane, conversation-pane, live-conversation-switch, live-conversation-unanswered-send. Only the original feedback run executes. These are diagnostic results without catalog receipts.

Concrete source comparison: feedback case and fixture, dependency locks, requirements, and AGENTS are byte-identical. No product files underweb/src orassets differ. All587 imported raw sources match in the single-case build. The shared build has602 raw sources. Its only difference is the generated workspace host. That host chunk is outside the feedback entry dependency graph. All reachable feedback CSS matches after Svelte scope normalization. Raw emitted JS/CSS hashes remain recorded. JS minifier aliases and worktree-derived scope names differ, so raw bundle equality is not claimed.

Passing controlled samples cannot attribute an intermittent earlier frame jump. Original multi-case execution and machine scheduling are not fully replayed. CDP attachment also differs from the original catalog launcher. The shared-build pair narrows the build-context gap but does not erase these limits. Experiments stop here.

Evidence: feedback-attribution-summary.json contains source hashes, build hashes, worst adjacent frame pairs, runtime facts and limitations. Each experiment directory retains scripts, build manifests, raw traces, logs and browser process metadata. Original failure evidence remains under root ownership.

Commands:

```sh
node .scratch/t_p1f3sn00-controls/feedback-attribution/run-pair.mjs > .scratch/t_p1f3sn00-controls/feedback-attribution/pair.log 2>&1
node .scratch/t_p1f3sn00-controls/feedback-attribution-shared/run-pair.mjs > .scratch/t_p1f3sn00-controls/feedback-attribution-shared/pair.log 2>&1
.venv/bin/python .scratch/t_p1f3sn00-controls/feedback-attribution/analyze.py
```
