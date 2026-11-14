# Native TypeScript server

Use TypeScript, Node APIs, and maintained JavaScript packages. Keep one shared helper for each job. Delete duplicate implementations when you change their callers.

- Own children through `lifecycle/owned_processes.ts`. Do not add another process owner. Keep cancellation separate from actual settlement.
- Put the shared flock binding in `core/locks/flock.ts`. Do not add another native binding or flock helper.
- Use `core/path_containment.ts` for lexical path containment. Lexical containment does not prove filesystem custody or prevent symlink races.
- Use `AbortSignal.timeout()` and `AbortSignal.any()` for operation deadlines and cancellation. If a wait must leave admitted effects intact, use `core/promise_observer.ts::observe_promise`. Use `conversation/shutdown.ts::observe_shutdown` only for shutdown with the shared absolute deadline. Do not copy deadline helpers.
- Copy defined environment values through `core/environment.ts::copy_environment`. Keep provider allowlists, identity overrides, and variable removal with their provider.
- Use plain Node errors and JavaScript values. Do not reproduce Python exception names, representations, numeric syntax, or formatting inside the server.
- Use the existing `tar` package for TAR archives. Keep hash-first admission, explicit path and link policy, size budgets, and owned cleanup around the package.
- Prefer Node APIs before a new dependency. A package must replace real code and add no native addon. Name each added package and its purpose in the review.

Preserve the HTTP API, executor wire protocol, and database schema. Preserve CLI command names, options, `--json` fields, and exit codes. Human text, help, and error wording are not CLI contracts. Python formatting belongs only at boundaries where an external contract requires it.

The native-style CI check scans added TypeScript lines against the pull request base. It rejects retired Python helpers and exception classes, fake Python error names, compatibility imports outside their wire consumers, Koffi imports outside `core/locks/flock.ts`, and new flock or deadline helpers. These small text checks cover ordinary import and definition syntax. They do not parse TypeScript or replace source review. Unchanged occurrences remain grandfathered.

Reuse focused behavioral coverage. Add a test only for a distinct failure risk that existing coverage does not prove. Keep production code independent of test helpers.
