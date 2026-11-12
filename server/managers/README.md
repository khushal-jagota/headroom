# Managers during the TypeScript migration

Manager and Chief pages use `core/http/numeric_json.ts`. Page intake retains each native JSON number's integer or float kind. Integer markers keep a bigint, so parsing never rounds their digits. The shared encoder writes Python's spaced JSON form. Native `JSON.rawJSON` emits markers as numbers in HTTP responses. Ordinary routes retain their existing parser.

Use `parse_numeric_json` before document validation and `stringify_python_json` for stored documents. `clone_numeric_json` preserves immutable numeric markers. Use `numeric_value` only for validation or calculations. Do not replace the stored marker with that projection. `ManagerPageDocument` exposes the marker form through a mapped type of the wire page contract.

This encoder supplies the page paths' `ensure_ascii=False` form with ordinary spaced separators. It does not supply arbitrary dictionary ordering or sorted-key/ASCII modes that these paths do not need. Page schemas reject arbitrary numeric-looking dictionary keys. Valid plain numeric fields must remain finite.

`configure_manager_documents` accepts four dependencies for the goals unit:

- `manager_goals(sql, manager_id, now)` reads held goals and returns their populated JSON within the supplied snapshot.
- `prepare_rich_document(document)` prepares chart/category/formula shapes while it preserves numeric markers.
- `validate_page_bindings(sql, document)` validates measure references, formula graphs, dataset/category references, and manual-control origins.
- `document_readings(sql, subject, now)` produces rich readings for the supplied holder identity, document, and revision.

Plain documents work without these dependencies. A held goal or chart requires its dependency explicitly. Empty goal or readings arrays are not evidence of rich behavior. The goals unit must compose the dependencies and rerun all six page routes with populated data.

The wake loop remains behind `ServerOptions.managerWakes`, which defaults to off. The lifecycle unit controls later production composition and dispatch admission. Its test-only pass route is one owned factory entry in `test_host.ts`. That factory runs only after the server acquires database ownership.
