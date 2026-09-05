# Final test measurements

The comparable inventory counts collected pytest cases and expanded Vitest
runtime cases. Legacy browser/Node scripts are excluded from both revisions: a
script may contain many assertions and is not comparable to one collected case.

| Suite | Baseline `b7ca8e047967e405feeebe058f5cd2ef82b2c2e5` | Pruning branch | Final integrated tree |
|---|---:|---:|---:|
| Python unit | 1,924 | 935 | 946 |
| Python integration | 14 | 8 | 8 |
| Playwright E2E | 39 | 19 | 19 |
| Vitest runtime | 528 | 178 | 178 |
| **Total** | **2,505** | **1,140** | **1,151** |

The integrated tree removes 1,354 cases, or 54.1% of the baseline. At least half
of an odd 2,505-case baseline means removing at least 1,253 and retaining no more
than 1,252, so the final result has 101 cases of margin.

Python suites were collected separately because their nested `conftest.py`
plugins are incompatible when loaded together:

```text
.venv/bin/pytest --collect-only -q -o addopts= tests/unit
.venv/bin/pytest --collect-only -q -o addopts= tests/integration
.venv/bin/pytest --collect-only -q -o addopts= tests/e2e
```

Vitest was listed from `web/` with typechecking disabled:

```text
node_modules/.bin/vitest list --typecheck.enabled=false --no-color
```

That command imports and collects the tests, expands `it.each` into runtime
cases, and does not execute test bodies. Disabling typechecking prevents the
typecheck project from duplicating the runtime list. The earlier static frontend
estimate of 332 was therefore replaced by the actual 528-case baseline list.

The complete gitignored collection logs existed at measurement time with these
SHA-256 hashes:

```text
baseline-vitest-runtime-list.txt     3bc50966de0ed1ce4b39c9599e665efec287bd24075cd7c1df3e6a3f05f09402
integrated-unit-collection.txt       12fb0b7ecd25d55186445c6aa1ff63dd7e014917e32ee263a6e2ca105439417b
integrated-integration-collection.txt d627c53a1aaa2076411f4e6b06e7bf2407d0b914ec93d59f06d382b1b8ede834
integrated-e2e-collection.txt        3084790f1fdd7ea74de44af8311d491274574a0c4316f6d8febacc7570a5c4cd
integrated-vitest-runtime-list.txt   42e3c3087606742b7af8237b05efbbbd81138c0b417b0288c0a4f5a36f75d511
```

The pruning branch's 1,140 total is retained as historical evidence of the
deletion work before feature integration. The 1,151 integrated total is the
acceptance measurement.

The final full verification passed. Its Vitest output reports 334 tests because
that invocation includes the typecheck project; the runtime-only inventory above
prevents those repeated source cases from inflating either count. The 20 opt-in
real-provider tests remain collected but skipped in the normal local run, as at
baseline. No cases were newly skipped to meet the reduction. Full final output
is preserved in [`verify.log`](verify.log).
