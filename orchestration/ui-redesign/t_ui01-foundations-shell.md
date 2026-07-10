# t_ui01 — Foundations + shell

Reference: every mockup's `:root` + `.shell` block in
`orchestration/daily-redesign/*.html`; PLAN.md's type normalization table.

## Outcome
The serif face is available app-wide as tokens; the shell nav matches the
mockups (including the presence count). No screen content changes in this wave.

## Contract
1. **Fonts.** Add Newsreader self-hosted: add `@fontsource/newsreader` to web/package.json + lockfile (npm install) (weights
   400/500/600, normal style; italic NOT needed — the design uses no italics
   except input placeholders, which use the UI face) imported from
   `web/src/main.ts`. No CDN links anywhere. If fontsource lacks what's needed,
   vendor woff2 under `web/src/assets/fonts/` with `@font-face` in `app.css`.
2. **Tokens** (`assets/tokens.css`): add `--font-serif` and the six serif type
   tokens exactly as PLAN.md's table. Touch nothing else in the file.
3. **Shell nav** (`web/src/App.svelte` + its styles in `assets/app.css`):
   restyle to the mockup shell — 50px bar, hairline bottom, links faint→strong
   on hover/active, amber count badge on Review (existing `nav-badge`
   behavior/data preserved). Add the **presence count** right-aligned: a small
   spinner (existing stage-mark-spin idiom, 8px) + `N working`, sourced from
   `running_agents` on the already-polled queues resource (extend the App-level
   queues resource type to include `running_agents` — the API already returns
   it). Hidden when 0 (`0 working` shows nothing).
4. Presence element gets `data-shell-presence` for tests; add a unit/e2e-free
   check only if trivial — no new e2e file in this wave.

## e2e / selector notes
- `nav-link`, `nav-badge`, `data-screen` attributes on links are asserted in
  tests — grep `tests/e2e` for `nav` and preserve names and values.
- No other surface may change appearance beyond the shell and the font assets
  loading (adding the fonts must not alter any current screen — nothing uses
  `--font-serif` yet).

## Acceptance
`cd web && npm run build && npm run check && npm test` green; the shell renders
per mockup; `data-*` inventory unchanged except the added `data-shell-presence`.
