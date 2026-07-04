# T02 plan review (codex) — dispositions

Codex reviewed the T02 ticket-as-blueprint against SPEC §18.2/§18.3. Six findings; all addressed in the revised ticket:

1. **Gate order** — accepted. Skip-scan is a preflight, then gates run in the spec's stated order: ruff, mypy, unit, build check, e2e.
2. **Scoreboard suppressed on scan abort** — accepted. On a scan violation, verify prints the explicit message naming file+pattern AND still prints the full 36-item scoreboard (all FAIL — a tainted suite has no valid results) and `VERIFY: 0/36 PASS`.
3. **Substring token matching** — accepted. Matching is anchored: a test satisfies item NN iff its name starts with `test_aNN_` / `test_eNN_` (underscore required after the token).
4. **Vacuous passes** — refuted as instrument scope. The instrument proves the named tests ran and passed; assertion strength is enforced by the §18.3 fences and the codex audit, not by `./verify`. Recorded as a documented limitation in verify.py's header comment.
5. **Item-21 coverage vs six patterns** — accepted for the scan (implements all six: skip mark, pytest.skip(, xfail, .only, commented-out test, empty body). The item-21 named test asserts at minimum the four patterns the item text lists, plus the remaining two — added assertions strengthen, never weaken.
6. **Missing junit XML** — accepted. A missing/unparseable junit file scores every item of that suite FAIL; the scoreboard always prints; verify never crashes on gate failure.
