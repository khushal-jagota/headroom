# Canonical verification

The first foreground `./verify` attempt was externally terminated with exit 143 during
the frontend Node test chain. It produced no verifier verdict. Four exact untracked ACP
runtime fixtures left by that interrupted process were removed, and no verifier child
remained.

The warranted second attempt ran the unchanged settled implementation as a detached process
so the tool session could not terminate it. Its complete 162-line output is retained at:

`data/verify/t_w5gmk3cq-attempt-2.log`

SHA-256:

`bfe8f131d67dd009df18ddbc5a4ff06cb3667cfb63ffcddaa66ba38a30381815`

Result:

- Ruff: passed.
- MyPy: passed with no issues in 161 source files.
- Unit suite: 1,451 passed.
- Python compilation and CSS checks: passed.
- Svelte check: 0 errors and 0 warnings.
- Frontend build: 473 modules transformed.
- Frontend tests: passed.
- Playwright e2e: 129 passed.
- Final verifier marker: `VERIFY: PASS`.

