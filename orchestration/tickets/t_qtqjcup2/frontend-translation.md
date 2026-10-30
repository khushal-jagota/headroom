# Frontend copy and translation record

T3 source revision: `75e4ceb96489a2fe92c710c79dbea59778cccfd1`.

The owner permits natural TypeScript/Svelte at forced framework boundaries. Literal copies and upstream tests remain unchanged.

Full T3 MIT notice: `web/src/lib/usage/T3-LICENSE`. The copyright is T3 Tools Inc., 2026.

## Literal copies

All six source/destination SHA256 pairs match in [frontend-literal-hashes.json](frontend-literal-hashes.json).

| T3 file | Panels file | SHA256 |
|---|---|---|
| `packages/shared/src/usageMerge.ts` | `web/src/lib/usage/usageMerge.ts` | `26a6c303b49fd17921b7a67804e3011ac344d2dfcf5c35e183a66b49893edbfa` |
| `packages/shared/src/usageFormat.ts` | `web/src/lib/usage/usageFormat.ts` | `d6be32757fc4e24a118bfe320e75058a5ac5f593446ad4be25e371f278ab27ec` |
| `apps/mobile/src/features/usage/usageChartData.ts` | `web/src/lib/usage/usageChartData.ts` | `74cf258a202e282c58aa52c381041ff2c058b81f5342ad124458c799c4d63e7b` |
| `packages/shared/src/usageMerge.test.ts` | `web/src/lib/usage/usageMerge.test.ts` | `caa6e02c74d87967a0d6941ca4f47dfce51151443d071c35992a036d037e1cc5` |
| `packages/shared/src/usageFormat.test.ts` | `web/src/lib/usage/usageFormat.test.ts` | `76d88cb03e5a9740d698bb1ae69ee07e8ecdbf20716b8e3b64f3aa251a0719b4` |
| `packages/shared/src/usageLimits.test.ts` | `web/src/lib/usage/usageLimits.test.ts` | `39a828baccb29519714f4125c03e2e074f48d878db38a4849887bf40b85ae8d0` |

`usageChartData.test.ts` does not exist beside the source module. No upstream chart test was omitted.

## Forced substitutions

| T3 file and lines | Panels file and lines | Framework substitution |
|---|---|---|
| `packages/shared/src/usageLimits.ts:22,742` | `web/src/lib/usage/usageLimits.ts:741` | Effect DateTime import and ISO formatting become native Date. All other bytes match after those two substitutions. |
| `packages/contracts/src/usage.ts:24–203` | `web/src/lib/usage/contracts/usage.ts:5` | Effect Schema declaration graph becomes matching readonly TypeScript declarations. UsageDay.make preserves the branded constructor and YYYY-MM-DD constraint. Constants preserve the source values. |
| `packages/contracts/src/providerUsageLimits.ts:20–167` | `web/src/lib/usage/contracts/providerUsageLimits.ts:5` | Effect Schema output declarations become matching readonly TypeScript field graphs. Local data uses these same allowance shapes. |
| `packages/contracts/src/usageLimitSourceId.ts:8–9` | `web/src/lib/usage/contracts/usageLimitSourceId.ts:6` | Effect string brand becomes its separate TypeScript brand and checked .make string constructor. |
| `packages/contracts/src/baseSchemas.ts:104–113` | `web/src/lib/usage/contracts/baseSchemas.ts:16` | EnvironmentId keeps its nonempty string constructor. Reachable scalar types retain their TypeScript output types. |
| `packages/contracts/src/providerInstance.ts:41–88` | `web/src/lib/usage/contracts/providerInstance.ts:9` | Provider brands retain the source slug pattern, length bound and distinct constructors. |
| `packages/contracts/src/server.ts:50–255` | `web/src/lib/usage/contracts/server.ts:5` | Reachable provider declarations preserve all fields, optionality and literal unions. isProviderAvailable is copied unchanged. |
| `packages/contracts/src/model.ts:7–43,125–128` | `web/src/lib/usage/contracts/model.ts:5` | Reachable model capability and descriptor declarations preserve their field and union shapes. |
| `apps/mobile/src/features/usage/usageProviders.ts:1–32` | `web/src/lib/usage/usageProviders.ts:12` | React appearance hook becomes the existing Panels dark appearance. Source labels, order and color selection remain. NativeWind warning colors resolve to their source palette values. |
| `packages/client-runtime/src/state/usage.ts:12–64` | `web/src/lib/usage/usage.ts:5` | Effect atom command/result and registry query become pricing HTTP command followed by query refetch. A failed price refresh still runs the summary read. |
| `apps/mobile/src/features/usage/UsageDailyChart.tsx:1–53` | `web/src/components/usage/UsageChart.svelte:10` | React useMemo becomes Svelte derived state. Native Views become HTML columns. buildChartDays, peak reduction, height formula and bottom-first stack remain. |
| `apps/mobile/src/features/usage/UsageRouteScreen.tsx:392–620` | `web/src/components/usage/RecordedUsage.svelte:15` | View/Text and React memoization become Svelte/HTML. Copied merge computes every total. Provider ranking, chart inputs, average, cache share and model rows retain source operations. Existing Panels provider filter selects buckets and sources before the copied merge. |
| `apps/mobile/src/features/usage/UsageLimitsPooled.tsx:69–257` | `web/src/components/usage/UsageLimits.svelte:17` | Local allowance map replaces environment atoms. collectLimitPools remains unchanged. Cards retain refill selection, column ordering, percentage and reset calculations. Native account navigation becomes an inline details disclosure. No redemption operation is exposed for the local-history source. |
| `apps/mobile/src/features/usage/UsageLimitsSection.tsx:47–98` | `web/src/components/usage/AllowanceWindows.svelte:11` | Native window rows become HTML meters. Remaining share, elapsed share, time marker, pace and countdown retain source operations. |
| `apps/mobile/src/features/usage/UsageRouteScreen.tsx:66–138,242–316` | `web/src/routes/BackendsRoute.svelte:22` | React state becomes Svelte state and TanStack queries. Existing Panels hash addresses and ordinary Config route remain. The selected 24-hour window uses literal makeWindow. Summary, limits and refresh go only through local usage HTTP routes. |
| `packages/client-runtime/src/state/usage.ts:29–58` | `web/src/lib/conversation/recordedUsageWire.ts:8` | Environment RPC becomes thin same-name-field HTTP input/output transport. Summary uses explicit T3 fields. Limits use the approved local map. Pricing refresh is distinct from native/provider refresh. |

The canonical `web/usage-aliases.mjs` map also supplies `web/tests/browser/run.mjs`, which otherwise ignores Vite config. Build/test/TypeScript aliases preserve literal imports for `@t3tools/contracts`, `@t3tools/shared/usageMerge` and `vite-plus/test`. ES2023 type declarations support unchanged upstream `toReversed` assertions. Runtime target remains ES2022.

The facade projects the named upstream contracts for the copied browser modules. It does not invent a balance field or map monetary credits into reset credits.

The local allowance source extension belongs to the server adapter. Browser transport consumes its T3 windows without another provider, authentication or native operation.

## Superseded implementation

The old components usagePresentation.ts, usageProviders.ts and pricing-only presentation test were removed. Their stored-only/null-gap and custom summary assertions conflict with the approved T3 copy. `recordedUsageWire.ts` now carries the T3 bucket summary. `queryCatalogue.recordedUsage` uses explicit T3 window inputs.

## Source case inventory

All upstream inputs and assertions remain byte-for-byte. The copied modules run 63 upstream cases: merge 12, format 6, limits 45, including parameterized rows.

| Test source = destination basename | Original line | Case |
|---|---:|---|
| `usageMerge.test.ts` | 77 | sums environments that read different transcript directories |
| `usageMerge.test.ts` | 97 | counts a shared transcript directory once |
| `usageMerge.test.ts` | 115 | drops only the duplicated provider, keeping the environment's other one |
| `usageMerge.test.ts` | 149 | excludes an environment reporting an older contract version |
| `usageMerge.test.ts` | 172 | keeps the previous compatible contract version so additive provider expansions still merge |
| `usageMerge.test.ts` | 198 | derives provider shares and cost quality |
| `usageMerge.test.ts` | 224 | keeps two machines apart when hostname and home path collide |
| `usageMerge.test.ts` | 240 | still collapses two servers reading the same directory |
| `usageMerge.test.ts` | 259 | totals sessions from per-directory distinct counts, not per-bucket sums |
| `usageMerge.test.ts` | 286 | returns empty totals with no environments |
| `usageMerge.test.ts` | 293 | omits providers with no sessions or usage |
| `usageMerge.test.ts` | 317 | derives hourly totals without losing the daily rollup |
| `usageFormat.test.ts` | 13 | enumerates 24 fixed buckets across a rolling window |
| `usageFormat.test.ts` | 21 | formats rolling instants in the requested time zone |
| `usageFormat.test.ts` | 27 | disambiguates repeated hours during a fall-back transition |
| `usageFormat.test.ts` | 32 | makes hourly tooltip dates relative to the window in its requested time zone |
| `usageFormat.test.ts` | 50 | builds an exact minute-aligned 24-hour request |
| `usageFormat.test.ts` | 58 | degrades an unknown resolved zone to UTC instead of crashing |
| `usageLimits.test.ts` | 59 | places the clock three fifths through a five-hour window with two hours left |
| `usageLimits.test.ts` | 66 | has no pace without a reset or a duration |
| `usageLimits.test.ts` | 72 | phrases the reset as a countdown |
| `usageLimits.test.ts` | 84 | explains empty bars and passes provider messages through |
| `usageLimits.test.ts` | 102 | keeps only usable providers whose driver reports limits at all |
| `usageLimits.test.ts` | 133 | labels environments only when more than one reports limits |
| `usageLimits.test.ts` | 211 | matches across environments even when the hub is visited before the native provider |
| `usageLimits.test.ts` | 223 | keeps other providers, other emails, and unidentified accounts with the same plan |
| `usageLimits.test.ts` | 258 | restores the hub account when the matching provider disappears |
| `usageLimits.test.ts` | 267 | keeps source errors and genuinely empty sources distinguishable from hidden accounts |
| `usageLimits.test.ts` | 285 | keys sources per environment and names the environment only when several have some |
| `usageLimits.test.ts` | 335 | merges one account reported natively on two environments and by a hub into one entry |
| `usageLimits.test.ts` | 392 | takes windows from a fresher hub read but credits and redeem from the native instance |
| `usageLimits.test.ts` | 437 | redeems through the hub when it holds a credit, even with a fresher native read |
| `usageLimits.test.ts` | 486 | redeems on the environment whose snapshot supplied the credits on show |
| `usageLimits.test.ts` | 515 | uses the freshest hub credit and its environment even when the account is also native |
| `usageLimits.test.ts` | 553 | keeps distinct hub accounts redeemable through their own source |
| `usageLimits.test.ts` | 584 | does not give old credits the timestamp of a newer window-only read |
| `usageLimits.test.ts` | 616 | names an environment once however many of its instances share the account |
| `usageLimits.test.ts` | 637 | keys a hub account without an email by hub, so two environments on one hub share it |
| `usageLimits.test.ts` | 656 | pools windows by id across accounts and orders resets by when they land |
| `usageLimits.test.ts` | 812 | keeps session columns across rows with opposite reset and usage orders |
| `usageLimits.test.ts` | 834 | preserves gaps without counting missing windows toward pooled quota |
| `usageLimits.test.ts` | 855 | falls back to weekly resets when no account reports a session |
| `usageLimits.test.ts` | 866 | sorts unknown resets last and breaks ties consistently |
| `usageLimits.test.ts` | 890 | names failures and silence, skips unsupported accounts, and labels environments only when several |
| `usageLimits.test.ts` | 958 | uses hub credit balances and redemption targets in the composer, including native duplicates |
| `usageLimits.test.ts` | 983 | redeems a native duplicate through the hub even when the native snapshot is fresher |
| `usageLimits.test.ts` | 1023 | keeps accounts and custom instances separate, filtering by driver |
| `usageLimits.test.ts` | 1064 | supports a source-only provider and keeps duplicates when the native probe failed |
| `usageLimits.test.ts` | 1087 | surfaces source errors only for sources that carry the selected driver |
| `usageLimits.test.ts` | 1112 | advertises global and workspace commands only for providers present in Limits |
| `usageLimits.test.ts` | 1155 | ignores quota movement but not the drivers offered the command |
| `usageLimits.test.ts` | 1171 | treats a failed read as a change in coverage, in both directions |
| `usageLimits.test.ts` | 1183 | inverts and clamps the reported usage |
| `usageLimits.test.ts` | 1192 | recognizes only the standalone local action |
| `usageLimits.test.ts` | 195 | prefers native %s limits by email without changing provider rows or source snapshots (parameterized) |
| `usageLimits.test.ts` | 249 | retains hub limits when the native provider cannot show them: %j (parameterized) |

## Additional boundary checks

`web/tests/recorded-usage.test.ts` reuses the existing Svelte SSR and HTTP test setup. It proves only the translation boundaries absent from upstream shared tests.

| Distinct risk | Existing coverage gap | Cheap faithful layer |
|---|---|---|
| Svelte loses merge values or empty hourly columns | Shared tests never render Svelte | SSR renders the actual copied merge/chart inputs. |
| Cost label or unpriced share changes during markup translation | Shared tests prove values, not text | SSR checks T3 cost wording and visible unpriced share. |
| Provider filter disconnects bucket and source ownership | Filter is Panels transport/surface glue | SSR selects both before copied merge. |
| Local allowance disappears at 100 percent or resets become credits | Source has no local-history adapter | SSR checks zero allowance, observation/reset and unavailable source. |
| Usage translation breaks ordinary Config navigation | Source mobile navigation differs | Existing SSR header fixture checks Config address. |
| HTTP adapter changes field names or invokes native refresh | Source uses RPC | Mock fetch checks actual request URLs and GET methods. |
| Price failure suppresses rescan | Source uses Effect command results | Injected HTTP-command failure proves rates then summary order. |

No browser host was added. The existing host remains available for the root agent’s real-history proof.

## Verification

Focused command: `npm --prefix web run test:vitest -- src/lib/usage/usageMerge.test.ts src/lib/usage/usageFormat.test.ts src/lib/usage/usageLimits.test.ts tests/recorded-usage.test.ts tests/usage-address.test.ts`.

The runtime scope passes 84 cases: 63 copied upstream cases, seven translation/transport cases, and 14 existing address cases. The compiler scope passes another 66 case declarations. Vitest reports 150 combined cases and no type errors.

`npm --prefix web run check` reports zero errors and zero warnings. `npm --prefix web run build` passes. Existing unresolved static CSS references and large-bundle advisories remain build warnings.

The existing `workspace-utility-pane` browser case prepares and bundles with its normal `configFile:false` host and the canonical aliases. That build-only check takes 7.129 seconds. It starts no server and executes no browser. Real-history browser proof remains the root agent’s separate connected check.

No provider/native/service or production action is part of these checks. No test-only implementation or replacement product is part of the proof.

## Connected-proof asset correction

The first connected product proof found an asset-path regression in the alias refactor. That edit accidentally removed the existing Vite `base: "/_app/"` setting. The isolated app serves compiled assets under `/_app`, so the incorrect build requested missing `/assets/index...` files and never mounted Usage. The base is restored. The production output is rebuilt with `/_app/assets/...` references. No test assertion changed. The existing browser host retains its explicit `base: "/"` for temporary fixture output.

The remaining configuration diff preserves existing settings: Vite gains only the alias import and resolver, Vitest also includes copied source tests, TypeScript adds aliases/extension support/ES2023 declarations, and the browser host gains the canonical resolver. No other pre-existing configuration setting was removed.

Correction checks: production build passes in 7.11 seconds. Every `/_app/assets/...` entry in the built index exists in `web/dist/assets`. An attempted asset-only HTTP check could not connect to the supplied isolated proof port after that listener stopped. No server action followed. The root agent resumes the connected browser check.
