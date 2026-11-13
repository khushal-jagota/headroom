# Native TypeScript server

Use TypeScript, Node APIs, and maintained JavaScript packages. Keep one shared helper for each job. Delete duplicate implementations when you change their callers.

- Own children through `conversation/backends/codex_app_server/process.ts`. Do not add another process owner. Keep cancellation separate from actual settlement.
- Put the shared flock binding in `core/locks/flock.ts`. That module is forthcoming. Do not add another native binding or flock helper.
- Use `core/path_containment.ts` for lexical path containment. That module is forthcoming. Lexical containment does not prove filesystem custody or prevent symlink races.
- Use `AbortSignal.timeout()` and `AbortSignal.any()` for operation deadlines and cancellation. If a wait must leave admitted effects intact, use `core/promise_observer.ts::observe_promise`. That observer is forthcoming. Use `conversation/shutdown.ts::observe_shutdown` only for shutdown with the shared absolute deadline. Do not copy deadline helpers.
- Copy defined environment values once through the shared primitive. Keep provider allowlists, identity overrides, and variable removal with their provider.
- Use plain Node errors and JavaScript values. Do not reproduce Python exception names, representations, numeric syntax, or formatting inside the server.
- Use the existing `tar` package for TAR archives. Keep hash-first admission, explicit path and link policy, size budgets, and owned cleanup around the package.
- Prefer Node APIs before a new dependency. A package must replace real code and add no native addon. Name each added package and its purpose in the review.

Preserve the HTTP API, executor wire protocol, database schema, and CLI contracts. Their externally required shapes, names, types, statuses, options, output, and exit codes remain authoritative. Python formatting belongs only at those boundaries when the contract requires it.

The native-style CI check compares changed TypeScript files with the pull request base. It rejects new Python representation imports, Koffi imports outside `core/locks/flock.ts`, and flock or deadline helper definitions. Existing occurrences remain grandfathered. This check does not authorize new Python imitation or replace source review.

Reuse focused behavioral coverage. Add a test only for a distinct failure risk that existing coverage does not prove. Keep production code independent of test helpers.
