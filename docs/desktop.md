# The desktop app

The desktop app is Panels as a Mac app. It is an Electron app that carries the same Svelte screens as the browser version, and it is a window on one Panels server. It does not run a server.

## What it is made of

The app has two sides and a bridge between them.

- **The screens** are the `web/` build output, packed inside the app with the `assets/` and `static/` files they load. They run in the sandbox, with context isolation and no Node.
- **The native side** (the Electron main process) holds the one server address, serves the screens, and does the jobs that need the operating system: menus, the window, notifications, and opening links in the browser.
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

The screens keep the relative paths they use in the browser. The app serves them from its own scheme, `panels://app`, and the native side forwards every `/api` and `/files` request to the server address. So the screens never need the address, and server paths in the page, such as icons, videos and artifact frames, work as they do in the browser. Only the app's own pages may write through the forward: a write from a page of another site, such as one opened inside an artifact frame, is cancelled.

Requests reach the server from the native side with no Origin header. The forward adds `X-Panels-Client` to every `/api` and `/files` request and replaces any value from the screens. It identifies this installation, its app version, and its native bridge version. The server checks declared desktop versions before writes. Requests with no identity header remain accepted for older callers. Identity supplies version facts, not authority; see [Who may act](authority.md). A server behind Tailscale Serve still gets its identity header from Serve. When the server cannot be reached, the forward fails as a network error, so the live streams reconnect by themselves when it is back.

The native probe and notification stream send the same installation identity. `desktop/src/bridge_version.ts` defines the bridge version separately from the app version. The updater reads the server's minimum bridge version through `fetchDesktopCompatibility` in `desktop/src/server_meta.ts`. This helper rejects an unavailable or invalid metadata answer. The service worker, which only serves web push, does not run in the app.

## The server address

On its first start, the app asks for a Panels server on this Mac, at `http://127.0.0.1:8767`. If one answers, the app keeps that address. Otherwise it shows the address page, which takes an address, such as a Tailscale address, and checks that a Panels server answers there. "Change Server…" in the app menu opens the same page. This page is temporary: the setup interface replaces it.

## The last session

The app opens with its last state. The native side remembers the screen the window was on and reopens there. The screens keep their server reads and the last navigation view in their own IndexedDB, so the next launch shows them at once, before the server answers. Every restored read then refreshes from the server. Until fresh data replaces the saved copy on screen, the shell status says "Showing your last session", or that navigation is reconnecting. Writes still go to the server, so nothing changes while it cannot be reached.

A saved copy from another build of the screens is not used, because response shapes can change. When the person changes the server, the native side deletes the screens' IndexedDB and forgets the last screen, so one server's Tickets never show under another. That also drops any queued outgoing messages, which belong to the old server. The browser version does none of this.

## Mac behavior

The app has the standard Mac menus. The window opens where it was last left, at the same size. Closing the window does not quit the app; a click on its Dock icon brings the window back.

## App updates

The native side uses electron-updater to download the newest stable desktop release quietly. It selects desktop release tags explicitly, so a server release cannot become an app update. A ready update installs when the app quits, or after the app remains without a window. A reopened window cancels that delay. A failed check or download leaves the installed app usable.

The main process owns the restart dialog when new screens require a newer shell. The dialog offers a restart after the update is ready. It adds no bridge action for artifact pages.

The desktop workflow signs and notarizes the app independently of server publication. A release to main publishes its ZIP and matching updater metadata on GitHub Releases. Manual runs produce two signed builds for a private feed and publish no release. See the [private update proof](../ops/desktop/private-update-proof.md).

## Notifications

The native side holds a live stream to the server, `/api/notifications/desktop`, while the app runs, also with its window closed. Each event on it is one notification that the server's [notification rules](notifications.md) allowed, with the same words as on the other devices. The native side shows it through macOS, and a newer one for the same Ticket, Manager or Chief replaces the older one. A click brings the app forward and opens that place in the window, and opens the window if it was closed. When the app quits, the stream ends and the Mac gets no more notifications. The stream reconnects by itself after the server comes back, and it moves to the new server after "Change Server…". The switches in Settings are the server's, the same for every device. In the app, the device row says that the Mac gets the notifications, and macOS Settings control banners and sounds.

**Known limit: no replay.** The app shows only notifications that arrive while it is connected. A notification sent while the app is closed, the Mac sleeps, or the connection is down does not reach the Mac later. The browser and iPhone still get it.

## Artifacts and the bridge

Artifact HTML runs at the screens' own origin, so a script in an artifact can reach the bridge. Today the bridge has only the address calls, and they answer the address page alone. **Do not add a bridge call that acts until artifacts are isolated.**

## Where the code lives

`desktop/` holds the app: `src/main.ts` (lifecycle, window, menus, links), `src/app_scheme.ts` (the scheme and the forward), `src/server_address.ts`, `src/last_session.ts`, `src/notifications.ts` (the stream and macOS notifications), `src/updates/` (native update checks and installation), `src/bridge.ts` (the bridge type), and the address page. The screens' side of the last session is `web/src/lib/savedSession.ts`. Its build packs the `web/` build. The checks in `tests/e2e/desktop/` drive the built app with Playwright under a virtual display against a test server, through `tests/e2e/desktop.config.ts`. The "Desktop app" workflow runs them.

_Last verified: 2026-11-22._
