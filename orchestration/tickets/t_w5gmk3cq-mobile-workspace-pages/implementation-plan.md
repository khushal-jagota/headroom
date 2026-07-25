# Implementation plan

1. Add focused mobile Playwright coverage in `tests/e2e/test_chief_of_staff.py` for
   both Workspace selections and run it to meaningful RED against current production.
2. Update the two `BoardRoute.svelte` selection handlers to branch on
   `window.matchMedia("(max-width: 960px)").matches`, choosing the existing standalone
   hashes on narrow screens and preserving current Workspace hashes otherwise.
3. Update `docs/frontend.md` and rebuild the tracked frontend output.
4. Run focused Playwright, Svelte check/build, and diff checks; record exact results.
5. Capture the two real mobile destination screenshots for the Ticket implementation
   proposal.
6. Obtain independent diff review, address every finding, then let the parent run the
   one canonical `./verify`.

