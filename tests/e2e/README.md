# Browser gates

These Playwright tests check the TypeScript server in a real Chromium browser. Each
test starts its own server on a fresh database and stops it at the end. The database is
built from `server/core/database/application_schema.json` by
`server/installation/database_main.ts`, the same path a fresh install takes. No Python
runs.

Use a real browser test only when a risk needs a real browser and a live server
together. Test other browser behavior in `web/tests`.

## Fixtures

Import `test`, `expect` and the helpers from `support/fixtures.ts`.

- `server` starts `server/main.ts`, the production entry.
- `railServer` and `signalServer` start `server/test_host.ts`. It adds scripted
  backends and the `/api/test` routes.
- `newContext` makes a browser context. When a test fails, the context keeps a trace,
  screenshots and page HTML.
- `api` calls the HTTP API as the owner. `cli` runs the TypeScript CLI against the
  test's server.
- `agentHeaders` and `proposeAsWorker` act as a Ticket's Worker, a Manager or the Chief.
  No route issues a launch token, so they use the server's token issuer on the test
  database.

## Running

The `browser` job in `.github/workflows/typescript-backend.yml` runs these tests on its
schedule and on manual runs. Run them on hosted CI, not on the VPS. To run them on a
development machine:

```sh
npm ci --prefix server
npm ci --prefix web
npm ci --prefix tests/e2e
(cd web && node node_modules/vite/bin/vite.js build)
cd tests/e2e
node_modules/.bin/playwright install chromium
node_modules/.bin/playwright test
```

Run `node_modules/.bin/tsc --noEmit -p .` in `tests/e2e` to typecheck the tests.
