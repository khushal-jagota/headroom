# Lane C — Ticket-constructor fixtures

Root handled this two-line trivial fixture repair while the other disjoint lanes ran in parallel.
The direct `Ticket(...)` helpers in `test_generic_field_storage.py` and `test_value_edit_logic.py` now
supply the already-required `employee_backend="hermes"`. No product source or assertion changed.

Focused result:

```text
.venv/bin/pytest tests/unit/test_generic_field_storage.py tests/unit/test_value_edit_logic.py
24 passed, 1 warning in 0.15s

.venv/bin/ruff check tests/unit/test_generic_field_storage.py tests/unit/test_value_edit_logic.py
All checks passed!
```

No full `./verify`, server, browser, package, generated-asset, docs, memory, or unrelated file action
was performed.
