The complete browser catalog is green on exact ticket revision `16bdfe4f975548f5b474827c70a6df7ddf63819f`. All 14 declared cases pass. The required typed companion and TypeScript compiler pass. The source branch stays at this checked revision. Reports live on this separate evidence ref.

The earlier feedback animation failure remains unattributed. “Unchanged” in the first proposal meant that the case source was unmodified. It did not establish that the failure predated this branch.

## Matched feedback comparisons

The comparison uses staging `c1d99e049b3d8daa121c21cd0a88ac8d9234d546` without the barrier branch and ticket revision `974aee1a9541241bc63625ea5fd8d15c84102329`. Both run on the same physical host and Chromium process. Each run uses fresh contexts, the same origin and URL, and the original complete case, viewport, input, and clock behavior.

| Distinct diagnostic context | Staging result | Branch result | Staging maximum | Branch maximum |
| --- | --- | --- | --- | --- |
| Feedback-only build | Pass | Pass | 6.19 px | 6.36 px |
| Original five-case shared build, feedback first | Pass | Pass | 6.21 px | 6.21 px |

Each tree runs once in each context. The second experiment changes the build context to match the original failure. It is not a repeated gate attempt. Neither experiment changes the case or its strict limit below eight pixels. No further diagnostic repetitions occur.

The observed 10.97-pixel step does not recur. These passing samples establish neither a pre-existing defect nor branch causation. The remaining gap is a failing trace under comparable conditions that distinguishes either cause. Host scheduling varies, and diagnostic CDP attachment differs from the original catalog launch. The attribution report preserves these limits.

Source inspection supports independence. All 587 imported sources match in the single-case build. The shared build has 602 imported sources. Its only source difference is the generated workspace fixture, outside the feedback host's import graph. Product source, assets, dependency pins, and the feedback case match. Normalized feedback CSS also matches. This evidence does not substitute for reproduction.

## Exact final catalog

Staging `c1d99e04` was brought into the ticket branch through merge `0c948ba96856f5f253681d37b8f95bdf04e4922c`. Documentation commit `16bdfe4f975548f5b474827c70a6df7ddf63819f` identifies the proof location. The four barrier source files remain identical to reviewed revision `6621efe8`.

The full catalog runs once on the final candidate. It requests all eight entries and all 14 cases. Every scenario selector is absent. The incoming selector admission rule stays active. There is no filtered receipt or exception.

- Checked revision: `16bdfe4f975548f5b474827c70a6df7ddf63819f`
- Invocation: `a0fe9471-7e72-4601-81fd-2ee6cc5fd9c2`
- Catalog: 14 of 14 cases pass, 421.983 seconds.
- Required typed companion: pass.
- TypeScript compiler: pass.
- Case, companion, invocation, revision, and declared selection match in the raw receipt.

The run changes no source. The report commit is separate so that the source branch head remains the exact checked SHA. The original failed reports remain in the parent evidence directory. The complete catalog result does not claim unrelated test suites or a production build.

## Other checks and review

Ruff passes on all changed Python files. Helper mypy and configured project mypy pass, the latter across 644 files. The browser runner and deployment command contract tests pass, two tests. Fixture syntax and diff checks pass. AST comparison retains every original assertion and expect expression, 796 total.

Independent review confirms clean staging integration, unchanged barrier source, retained selector admission, and no unresolved source findings. Attribution remains an explicit limitation.

No pull request merge or deployment occurred. Implementation still needs approval. The later staging pull request must retain this checked source. If integration changes the final source revision, its catalog proof must correspond to that revision before merge. The ordinary `mypy (merged tree)` requirement also remains.

The baseline worktree remains at `/home/vps/projects/panels/worktrees/t_p1f3sn00-feedback-baseline` for the attribution record. Its services and browser are closed. The attribution archive preserves scripts, traces, source hashes, and logs under their original scratch paths. Keep the verification ref as the durable evidence location.
