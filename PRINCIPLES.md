# Engineering & Design Principles

These are standing rules. They apply unless a live owner decision overrides them.

## Codebase structure
- Top level is organised by semantic domain (e.g. rooms/, sharing/, billing/, games/<game>/). Within every domain, organise by kind of work: logic/algorithms together, data layer together, components together, views together. Layers within domains — never feature-slices within domains, never one flat pile.
- If something is non-trivial, it gets a folder. Folders are cheap and create structure that lets things expand or be isolated later.
- Anything intended to be tuned later (sizes, formulas, timings, limits) lives in an isolated configuration module, not inline.

## Core/module contract
- Shared infrastructure (core) and features (modules) are separated by a defined contract. A module declares its configuration, states/phases, and views; core provides the shared services and renders from a registry.
- Modules register through a single manifest/registry. Core contains zero module-specific conditionals — if core has an `if` on a module id, the structure is wrong.
- The test: adding the next module means creating a new folder and one registration line. If it requires changing core, stop and fix the structure.

## Contracts first
- Before implementation: generate the skeleton — schema, enums, interfaces, registry shape, design tokens, and a contracts/types file per domain.
- All implementation imports its types from contracts. Never redeclare a shape locally; never let the client invent its own version of a server type. If a contract must change, change the contract file and let type errors drive the fixes.

## Pure logic
- All business rules are dependency-free: plain functions in each domain's logic/ layer, zero imports from the backend framework, UI framework, or anything with side effects. Backend functions call rules; they do not contain them.
- The acid test: if a rule can't be unit-tested with no mocks, it's in the wrong place.

## External services
- Every external service sits behind an adapter interface. No provider types, URLs, or response shapes exist anywhere outside its adapter. Swapping providers touches one new adapter file and its registration, nothing else.
- Fetch once, own forever: when external data enters application state, persist the full record; downstream reads come from our store, never a re-fetch.
- Do not build fallback providers, retry choreography, or speculative resilience unless the spec demands it. A failed call gets a calm retry state.

## Design system
- Everything themable lives in one token file: two neutral surfaces, recessed and ink surfaces, relative hover and scrim effects, floating shadow, text tokens, accent (bright/surface/text), radius scale, motion durations (fast/base/slow), spacing scale, and border widths. The app's entire personality must be tunable by editing this one file.
- New token categories require evidence of need. Do not invent them speculatively.
- Minimal means selectively punchy, not timid: restrained surfaces, then deliberate moments of full impact. Add nothing to the UI unless it makes the user feel something or a smart person genuinely needs it to understand the screen. No explanatory text for the obvious.
- Type: strict scale, five sizes maximum. Hierarchy and spacing rhythm create calm.
- Motion: smooth and decisive, never slow. Entrances ease out. Nothing bounces. All durations come from motion tokens. Long animated traversals decompose into distinct beats rather than one heroic continuous move.
- Prefer making the design system carry function over bolting on widgets (e.g. a border draining as a timer rather than a countdown number). Structural elements doing double duty beats added chrome.
- Plan the component inventory for the whole flow before writing components. Build few, reuse hard. No near-duplicates.

## Test discipline
- Requirements and design decisions are stated concretely — decisions with criteria, never vibes or open qualifiers.
- Acceptance tests assert specific stated values, never vibes. Tests evolve deliberately with the design; when one changes, the change is intentional and recorded on the owning ticket, never a silent weakening.
- Verification means running the check fresh and showing full output. Results are never asserted from memory.
- End-to-end tests exist only for a material risk that requires a real browser and live server together. Use a frontend, unit, or integration test when that cheaper layer proves the same contract.
- A Ticket plan or review that adds or changes end-to-end coverage states the material risk, the exercised boundary, and why cheaper layers cannot prove it.
- Keep the smallest end-to-end proof that protects the risk. Do not use end-to-end tests for route catalogs, presentation details, exact geometry, or contracts already proved at a lower layer.
- A test names the product choices it depends on and pins none that it does not own. Defaults someone is free to change — which view a screen opens on, which group arrives open — are asked for in the test, never assumed.
