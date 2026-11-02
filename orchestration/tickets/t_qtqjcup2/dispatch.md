The approved Plan copies T3 usage revision `75e4ceb96489a2fe92c710c79dbea59778cccfd1`.

The worktree started at `d0c91a9c` for research. Before implementation, it fetched and advanced to `origin/staging` at `d9f6500b9cb807aa3970d9abdc51c4a11516c076`, as Chief directed. No merge conflict occurred.

File ownership:

- Server core worker: `UsageService`, `usageTranscriptReader`, `usageTranscripts`, `usageScanCache`, `usageAggregation`, `usagePricing`, and their translated tests.
- Browser worker: shared copies, contract facade, browser transport and Usage components, browser build/test aliases, and corresponding tests.
- Allowance worker: `localUsageLimits`, `codexUsageLimits`, `claudeUsageLimits`, `providerUsageLimits`, `UsageLimitSources`, `cliproxyApi`, and their tests.
- Primary worker: authoritative contract declarations, thin application wiring, integration repairs, verification, evidence, independent review, and final proposal.

Source contracts are T3's named declarations. Workers translate them mechanically and do not invent or alter shapes. The contract files precede implementation. Separate scopes permit work in parallel without concurrent file edits.

The Plan received independent review before implementation. An independent diff review follows the completed work. The callback and adapter cache are approved functional extensions, separate from language plumbing.

Chief authorized an isolated worktree virtualenv and local Node development dependencies. Production, managed homes, controlled peers, system packages, native refreshes, provider requests, deployment, and release dispatch remain excluded.
