# The desktop app

The desktop app is Panels as a Mac app. It is an Electron app that carries the same Svelte screens as the browser version, and it is a window on one Panels server. It does not run a server.

## What it is made of

The app has two sides and a bridge between them.

- **The screens** are the `web/` build output, packed inside the app with the `assets/` and `static/` files they load. They run in the sandbox, with context isolation and no Node.
- **The native side** (the Electron main process) holds the one server address, serves the screens, and does the jobs that need the operating system: menus, the window, and opening links in the browser.
- **The bridge** is one typed object that the preload script gives each page as `window.panels`. One type file defines it, and each call is answered by the native side.

```text
 Panels app
 ┌───────────────────────────────────────────────────────┐
 │ screens (sandbox, no Node)    address page            │
 │   relative /api, /files ──┐          │ bridge         │
 │                           ▼          ▼                │
 │ native side:  panels://app scheme, bridge answers     │
 │   serves the bundle, forwards /api and /files ────────┼──▶ server
 └───────────────────────────────────────────────────────┘
```

## How the screens reach the server

The screens keep the relative paths they use in the browser. The app serves them from its own scheme, `panels://app`, and the native side forwards every `/api` and `/files` request to the server address. So the screens never need the address, and every server path in the page, such as an icon, a video or an artifact frame, works as it does in the browser.

Requests reach the server from the native side with no Origin header. The server treats them as it treats any caller without one; see [Who may act](authority.md). A server behind Tailscale Serve still gets its identity header from Serve. When the server cannot be reached, the forward fails as a network error, so the live streams reconnect by themselves when it is back.

The app's copy of the screens carries no server release tag, so the server's guard against writes from an older web build does not apply to it.

## The server address

On its first start, the app asks for a Panels server on this Mac, at `http://127.0.0.1:8767`. If one answers, the app keeps that address. Otherwise it shows the address page, which takes an address, such as a Tailscale address, and checks that a Panels server answers there. "Change Server…" in the app menu opens the same page. This page is temporary: the setup interface replaces it.

## Mac behavior

The app has the standard Mac menus. The window opens where it was last left, at the same size. Closing the window does not quit the app; a click on its Dock icon brings the window back.

## Artifacts and the bridge

Artifact HTML runs at the screens' own origin, so a script in an artifact can reach the bridge. Today the bridge has only the address calls, and they answer the address page alone. **Do not add a bridge call that acts until artifacts are isolated.**

## Where the code lives

`desktop/` holds the app: `src/main.ts` (lifecycle, window, menus, links), `src/app_scheme.ts` (the scheme and the forward), `src/server_address.ts`, `src/bridge.ts` (the bridge type), and the address page. Its build packs the `web/` build. The checks in `tests/e2e/desktop/` drive the built app with Playwright under a virtual display against a test server, through `tests/e2e/desktop.config.ts`. The "Desktop app" workflow runs them.

_Last verified: 2026-11-22._
