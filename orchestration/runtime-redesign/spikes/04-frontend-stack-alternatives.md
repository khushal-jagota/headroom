# Spike 04 — Frontend stack alternatives to Svelte + Vite

**Goal (owner):** an independent review (codex, spike-adjacent) recommended replacing
the current no-build vanilla JS with **Svelte + Vite** — keep FastAPI/SQLite, drive a
small resource cache off the existing typed WS events so only affected components
re-render, and make streaming chat a persistent component. The owner wants the
**alternatives in the same sweet spot** — small reactive frameworks + a light build —
scored against the *same* criteria that made Svelte the pick. This is a read-only
comparison; nothing in the repo changes except this file.

Not in scope (codex already covered these as its options 2 and 3): hand-rolled no-build
with mounted controllers, and the heavy React-platform + TanStack-Query direction. This
spike sits *between* those extremes.

Framework facts below were web-checked for current (mid-2026) status; see Sources.

---

## 0 · What we're scoring against (the 7 criteria)

The exact criteria that made Svelte the pick, restated tersely:

1. **Right-sized reactivity** — component-local state + lifecycle + fine-grained targeted
   updates; *no* React-platform ceremony.
2. **Targeted invalidation** — the existing typed WS events (`entity_id`, `kind`,
   `payload`) drive a small resource cache; only subscribed components update. Kills the
   wholesale `route() → replaceChildren → render()` re-render (`assets/app.js:68`).
3. **Streaming-friendly** — a chat component owning its DOM, appending tokens locally
   while other ticket fields refresh independently. (Today chat has to hide transient
   state in module scope to survive the global flush — `assets/components.js` chat block.)
4. **Keeps FastAPI/SQLite** — a frontend-only change; no backend rewrite.
5. **Fits token-driven CSS** — doesn't fight the single `tokens.css` custom-property
   design system.
6. **Incremental migration** — from the DOM-builder style (`components.js` → components,
   `screens-*.js` → routes), screen-by-screen.
7. **Single-user local** — build-light preferred; "building is cheap" but *not*
   heavy-for-its-own-sake.

Two notes before the scores:
- **Criterion 4 is a wash.** None of these require a backend change — the backend already
  ships typed, append-only event rows (`core/ws.py`, `core/events.py`). It scores 5/5 for
  every candidate and does not discriminate; kept for completeness.
- The 7 criteria **under-weight** three axes that actually separate the field:
  **ecosystem/longevity, TS story, and authoring ergonomics.** Those are handled in the
  per-candidate "distinctive tradeoffs" rather than the raw score, and they carry the
  final recommendation.

---

## 1 · Scorecard

Each cell is 1–5 against the criterion above (5 = best fit). Svelte 5 is included as the
**baseline reference**, not a ranked alternative. Totals are out of 35 but read them as
**bands, not a leaderboard** — the top four are within a rounding error, and the real
separation is qualitative (next column group).

| Stack | 1 Reactivity | 2 Targeted inval. | 3 Streaming | 4 Keeps BE | 5 CSS | 6 Migration | 7 Build-light | **Σ/35** |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| *Svelte 5 (baseline)* | 5 | 5 | 5 | 5 | 5 | 4 | 4 | **33** |
| **SolidJS** | 5 | 5 | 5 | 5 | 5 | 4 | 4 | **33** |
| **Van.js** | 4 | 4 | 5 | 5 | 5 | 5 | 5 | **33** |
| **Preact + Signals** | 4 | 4 | 5 | 5 | 5 | 4 | 4 | **31** |
| **Lit** | 4 | 4 | 5 | 5 | 3 | 4 | 4 | **29** |
| **Vue 3 (+Vite)** | 4 | 4 | 5 | 5 | 4 | 3 | 3 | **28** |
| **Alpine.js** | 3 | 2 | 2 | 5 | 4 | 2 | 3 | **21** |

The qualitative axes the score hides:

| Stack | Reactivity model | Runtime (gzip) | Build story | Ecosystem / longevity | TS story |
|---|---|---|---|---|---|
| *Svelte 5* | Compiler + runes (signals) | ~1.6 KB | Vite required | Large, stable, funded | Very good |
| **SolidJS** | Runtime fine-grained signals | ~7 KB | Vite/JSX **or** no-build `solid-js/html` | Mid, stable API, 2.0 in flight | Excellent (first-class) |
| **Van.js** | `van.state` / `van.derive`, per-node | **~1.0 KB** | **None** (classic script or ESM) | Small, single-team, active (Feb 2026) | Good (typed, but thin) |
| **Preact + Signals** | VDOM + opt-in signals (sidestep VDOM) | ~3 KB core + ~1.5 KB signals | Vite **or** no-build via `htm` | **Largest of the small set** (React-compatible) | Excellent |
| **Lit** | Reactive properties + lit-html; `@lit-labs/signals` | ~5–6 KB | **None** required (decorators want one) | Standards-based; longest half-life | Excellent |
| **Vue 3.5/3.6** | Proxy reactivity; Vapor Mode (no-VDOM) | ~10–34 KB | Vite required | Very large, mature, funded | Very good |
| **Alpine.js** | Markup directives over Vue-style core | ~7 KB | None | Mid; MPA-oriented | Weak (markup-first) |

---

## 2 · The candidates

### SolidJS — the strongest true alternative

**What it is.** Runtime **fine-grained reactivity** via signals. A component function runs
**once**; `createSignal`/`createMemo` wire the exact DOM nodes that depend on a value, and
a change touches only those nodes — no VDOM, no component re-execution.

**vs Svelte.** This *is* the model the problem wants. Where Svelte reaches signals through
a compiler (runes), Solid's signal graph is the runtime primitive — the reference
implementation of "an event invalidates exact resources, only the bound DOM updates."
Solid's `createResource` maps 1:1 onto the resource cache codex sketched (`ticket:<id>`,
`board`, `day:today`), and a WS event calls `refetch()`/mutates a signal. Benchmarks put
Solid a hair ahead of Svelte 5 on raw update speed; the gap is narrow and irrelevant at
this app's size.

**Where it beats Svelte.** (a) It has a **genuine no-build path** — `solid-js/html` tagged
templates give the same reactivity without JSX or a compiler, so you can start buildless
and adopt Vite later (Svelte has no equivalent — it *is* a compiler). (b) Reactivity is
explicit and inspectable (plain signals) rather than compiler-rewritten `$state`.

**Streaming.** Native. A `createSignal` (or a store) per stream; token chunks append to
its own DOM node while sibling fields hold their own signals.

**Migration.** The one real cost: idiomatic Solid is JSX, a different authoring model from
`document.createElement`. But `make(tag, cls, text)` → JSX is a mechanical transform, and
the `html\`\`` escape hatch lets the migration start without touching the build at all.
Score 4, not 5, only because the polished ergonomics do want the compiler.

**TS/CSS.** First-class TS. Zero CSS opinion — `tokens.css` custom properties work
untouched; scoped styling is opt-in, never forced.

---

### Van.js — the sleeper that best fits this app's ethos

**What it is.** The world's smallest reactive UI lib (~1.0 KB gzip, zero deps, no build).
DOM is composed with plain functions — `van.tags.div({class}, ...children)` — and reactivity
is `van.state(x)` / `van.derive(...)`; binding a state inside a node updates **just that
node**.

**vs Svelte.** Van is the **shortest migration on the board**. The current codebase is
already a DOM-builder: `make(tag, className, text)` → `van.tags.div({class}, text)` is
nearly 1:1, and reactivity is opt-in per node — you convert screen-by-screen, keeping the
rest of the app byte-identical. No JSX, no compiler, no `.svelte`/`.vue` files, no build
step at all. The official `VanX` extension (~1.2 KB) adds reactive lists, a global app
store, and server-driven UI if the resource cache needs more than hand-rolled states.

**Where it beats Svelte.** Criteria 6 + 7 outright: **zero build**, **~1 KB**, and a
migration that reads like a refactor of the code that already exists rather than a
rewrite. For a single-user local app whose whole ethos is "restrained, build-light, not
heavy-for-its-own-sake," Van is the most honest expression of that ethos that *still*
gives a real reactive primitive to kill the wholesale re-render.

**Streaming.** Trivial and DOM-owning by construction — a `van.state` for the transcript,
`van.add()` to append a token row; nothing global can stomp it.

**The honest weakness.** Ecosystem and longevity. Van is young, effectively small-team,
with no router/devtools/large library pool — you'll hand-roll the resource cache, the
event router, and routing (though these are ~100 lines, and codex's Option 2 already
implied writing them). Its reactivity is fine-grained per-node but has **thinner
composition/lifecycle** than Solid or Svelte — hence 4 on criterion 1, not 5. This is the
insurance-vs-minimalism call, and it's the crux of the recommendation below.

---

### Preact + Signals — the ecosystem hedge at small size

**What it is.** A ~3 KB VDOM lib with the **React API**, plus first-party `@preact/signals`
for fine-grained reactivity that binds a signal straight to a text node and **bypasses VDOM
diffing** on hot paths.

**vs Svelte.** You get the React mental model (JSX, hooks) and its **enormous ecosystem** at
a fraction of the weight, *plus* signals for targeted updates. Critically, TanStack Query
now has a **native Preact adapter** (and `@preact-signals/query` bridges query-core to
signals) — so the "mature resource cache with dedupe/retry/invalidation" that codex reached
for in its heavy option comes at ~5 KB total here, not React's ~45 KB.

**Where it beats Svelte.** (a) **Longevity insurance** — the React API is the safest
long-term bet and the deepest library pool. (b) A **no-build escape hatch** via `htm`
(tagged-template JSX), so buildless Preact + hooks + signals is a supported path.

**vs the criteria.** Scores a notch below Solid/Van on 1 and 2 precisely because criterion
1 warns against *React ceremony* — hooks rules, `key`, `memo`, and a VDOM underneath are
exactly that ceremony, even if signals paper over the render path. Streaming is a 5 (signals
+ TanStack streaming patterns). Note Preact **11** is still beta and not production-ready as
of early 2026 — start on **10.x**.

---

### Lit — standards-based, no-build, one CSS wrinkle

**What it is.** Web Components: `LitElement` classes with reactive properties and `html\`\``
tagged templates (lit-html), shipping as standard ES modules with **no build required**.
Property changes trigger efficient binding-level DOM patches; `@lit-labs/signals`
(SignalWatcher mixin, tracking the TC39 Signals proposal) adds finer grain when wanted.

**vs Svelte.** Lit's bet is the **platform** — custom elements don't churn, so its half-life
is the longest of the field, and it needs no toolchain. `html\`\`` is a modest step from
`createElement`. Streaming is a 5 (a chat element owns its DOM; token append = a property
update).

**The wrinkle (why CSS = 3).** Lit uses **Shadow DOM by default**, which *scopes* styles —
direct friction with a single global `tokens.css`. Two real mitigations: (a) render into
**light DOM** (`createRenderRoot() { return this }`), keeping the global stylesheet in
force; and (b) CSS **custom properties pierce shadow boundaries by inheritance**, so
`--accent-bright` et al. reach shadow components regardless. Surmountable, but it's the one
candidate that fights the restrained global-CSS model out of the box, and light-DOM Lit
gives up the encapsulation that is Lit's main selling point. Reactivity is
element-granular by default (4, not 5) unless you add the signals mixin.

---

### Vue 3 — mainstream-safe, but the heaviest here

**What it is.** A full progressive framework: Composition API with `ref`/`reactive`
(Proxy-based fine-grained reactivity), Single-File Components, and — in 3.5/3.6 — **Vapor
Mode**, a compile-to-direct-DOM path that drops the VDOM and shrinks the baseline under
~10 KB.

**vs Svelte.** The batteries-included, maximally-hireable pick: mature docs, `vue-router`,
Pinia, `@tanstack/vue-query`. Reactivity is fine-grained; targeted invalidation is
well-trodden.

**Why it lands lower.** For a **single-user local** app it's the most machinery of the
"small" set — the SFC `<template>` syntax is the biggest conceptual jump from
`createElement` (render-fn/JSX exists but is off Vue's golden path), and even Vapor-trimmed
it's the heaviest runtime. It nudges the criterion-7 line ("not heavy for its own sake")
more than the others. It wins if you expect the surface to grow into a *team* product; that
isn't this app's framing.

---

### Alpine.js — wrong shape for this codebase

**What it is.** Sprinkle-on reactivity through **HTML attributes** (`x-data`, `x-text`,
`x-on`), ~7 KB, no build. Its reactive core is Vue-derived and fine-grained.

**Why it's the weakest fit.** Alpine is explicitly designed to **augment server-rendered
HTML**, not to be an SPA framework — you author behavior *in markup*, not as JS components.
This codebase does the opposite: it builds DOM in JS with `createElement` and has no
server-rendered HTML templates to decorate. Wiring typed WS events → a resource cache →
component subscriptions, and owning a streaming chat, all fight the markup-directive model
(2s on criteria 2, 3, 6). Adopting Alpine would mean **inverting** the app to
HTML-template-first — more rewrite than any framework here, for a model that doesn't match
the problem. Included for completeness; not a contender.

---

## 3 · Ranked shortlist + recommendation

**Ranked alternatives** (Svelte excluded as the baseline):

1. **SolidJS** — the purest fit for the exact reactive problem; ties Svelte on the
   scorecard and adds a no-build escape hatch Svelte can't have.
2. **Van.js** — the minimalism/migration winner; a real reactive primitive at ~1 KB with
   near-zero rewrite and zero build.
3. **Preact + Signals** — the ecosystem hedge: React-API safety + TanStack Query at small
   size, with an `htm` no-build option.
4. **Lit** — standards-based longevity and no build, minus one CSS wrinkle.
5. **Vue 3** — mainstream-safe but the heaviest; sized for a team product, not this one.
6. **Alpine.js** — wrong authoring model; not a real option here.

**Recommendation.** Keep it a **two-horse race between Svelte 5 and SolidJS**, and if I
must name the single alternative worth putting *in front of* Svelte, it's **SolidJS** — it
matches Svelte on every one of the seven criteria, is the reference implementation of the
signals-invalidation model this app needs, and uniquely spans "no-build now → Vite later."
The Svelte↔Solid gap is genuinely small: Svelte wins slightly on authoring ergonomics and
ecosystem size, Solid wins slightly on reactivity purity and the buildless path. Either is
a correct answer; I would not overrule the original Svelte pick, but I would not let it go
unchallenged by Solid.

The **sleeper worth a deliberate look is Van.js.** Given how much this specific app values
minimalism — single-user, local, a frontend that is *already* hand-built DOM, an owner rule
of "build-light, not heavy for its own sake" — Van.js is the option whose cost profile
matches the app's stated values most exactly: it kills the wholesale re-render and unlocks
streaming with a refactor rather than a rewrite, and adds **zero** toolchain. It loses only
on ecosystem insurance.

**The one honest tradeoff separating the top three:**

> **Ecosystem insurance + polished ergonomics (pay a build step)** — Svelte / Solid —
> **vs. radical minimalism + shortest migration + zero build (accept a young, small-team
> ecosystem and hand-roll the cache/router)** — Van.js.

Everything else is detail. Preact is the explicit hedge *between* those poles: React-family
longevity at small size, with a buildless path — pick it only if "must be a safe, deep
ecosystem" outweighs both the cleaner ergonomics of Svelte/Solid and the radical thrift of
Van.

**Bottom line:** Svelte 5 remains fully defensible, but **SolidJS is its equal on these
criteria and edges it on reactivity fit + a no-build path** — make it a two-horse race. If
the owner weights this app's minimalism ethos above ecosystem insurance, **Van.js** is the
sleeper that delivers targeted invalidation and streaming with the least change and no
build at all.

---

## Sources

- SolidJS reactivity / no-build `solid-js/html` — [docs.solidjs.com](https://docs.solidjs.com/advanced-concepts/fine-grained-reactivity), [@solidjs/html on npm](https://www.npmjs.com/package/@solidjs/html), [Road to 2.0 (Discussion #2425)](https://github.com/solidjs/solid/discussions/2425)
- Svelte 5 runes vs Solid signals / bundle sizes — [PkgPulse: SolidJS vs Svelte 5 vs React reactivity 2026](https://www.pkgpulse.com/guides/solidjs-vs-svelte-5-vs-react-reactivity-2026), [PkgPulse: compiler-first frameworks 2026](https://www.pkgpulse.com/guides/solidjs-vs-svelte-2026)
- Van.js size / state / VanX — [vanjs.org](https://vanjs.org/), [github.com/vanjs-org/van](https://github.com/vanjs-org/van), [VanX](https://vanjs.org/x)
- Preact signals / htm no-build / TanStack Query / v11 status — [What's New in Preact 2026](https://blog.openreplay.com/whats-new-preact-2026/), [Preact v11 no-build workflows](https://preactjs.com/guide/v11/no-build-workflows/), [TanStack Query Preact adapter](https://tanstack.com/query/latest/docs/framework/preact/overview)
- Lit shadow/light DOM, custom properties, signals — [Working with Shadow DOM (lit.dev)](https://lit.dev/docs/components/shadow-dom/), [Reactive properties (lit.dev)](https://lit.dev/docs/components/properties/)
- Vue 3.5/3.6 Vapor Mode / bundle — [Vue vs Svelte 2026 (WeBridge)](https://webridge.co/compare/vue-vs-svelte), [byteiota: React 19 vs Vue 3.6 vs Svelte 5](https://byteiota.com/react-19-vs-vue-3-6-vs-svelte-5-2026-framework-convergence/)
- Alpine.js is for server-rendered HTML, not SPA — [HTMX/AlpineJS/SSR/SPA guide (DEV)](https://dev.to/emil_valeev/htmx-alpinejs-ssr-v123-and-spa-easy-missing-guide-2d7f)
