# The desktop app

The desktop app is Panels as a Mac app. It is an Electron app that shows the Svelte screens of its server’s release, and it is a window on one Panels server. It does not run a server.

## What it is made of

The app has two sides and a bridge between them.

- **The screens** come from the selected server and stay in the app’s data folder. The app also carries a baseline copy. They run in the sandbox, with context isolation and no Node.
- **The native side** (the Electron main process) holds the one server address, serves the screens, and does the jobs that need the operating system: menus, the window, notifications, and opening links in the browser.
- **The bridge** is one typed object that the preload script gives each page as `window.panels`. One type file defines it, and each call is answered by the native side.

```text
 Panels app
 ┌───────────────────────────────────────────────────────┐
 │ screens (sandbox, no Node)    setup screens            │
 │   relative /api, /files ──┐          │ bridge         │
 │                           ▼          ▼                │
 │ native side:  panels://app scheme, bridge answers     │
 │   serves verified screens, forwards /api and /files ──┼──▶ server
 └───────────────────────────────────────────────────────┘
```

## How the screens reach the server

The screens keep the relative paths they use in the browser. The app serves them from its own scheme, `panels://app`, and the native side forwards every `/api` and `/files` request to the server address. So the screens never need the address, and server paths in the page, such as icons, videos and artifact frames, work as they do in the browser. Only the app's own pages may write through the forward: a write from a page of another site, such as one opened inside an artifact frame, is cancelled.

The forward retains Electron’s native response relay, so a stream that the page closes also releases its server connection. Requests reach the server from the native side with no Origin header. The forward adds `X-Panels-Client` to every `/api` and `/files` request and replaces any value from the screens. It identifies this installation, its app version, and its native bridge version. The server checks declared desktop versions before writes. Requests with no identity header remain accepted for older callers. Identity supplies version facts, not authority; see [Who may act](authority.md). A server behind Tailscale Serve still gets its identity header from Serve. When the server cannot be reached, the forward fails as a network error, so the live streams reconnect by themselves when it is back.

The native probe and notification stream send the same installation identity. `desktop/src/bridge_version.ts` defines the bridge version separately from the app version. The updater reads the server's minimum bridge version through `fetchDesktopCompatibility` in `desktop/src/server_meta.ts`. This helper rejects an unavailable or invalid metadata answer. The service worker, which only serves web push, does not run in the app.

The server names the digest of its installed screens and serves that exact copy. The app checks the digest and every file before use. It also prefetches the admitted waiting release, but activates it only after the server runs that release and the installed bridge meets its requirement. The window reloads without an app restart. The app scheme uses `cache-control: no-store` so Chromium cannot mix files from different copies.

The app keeps the current and previous copies, plus the bundled baseline. A failed start or a crash returns to the previous copy or baseline. The native menu can retry a rejected copy through the same verification and bridge gate. The baseline also supports first setup without a network connection. Old copies are removed after they are no longer needed. An older server gets a shell-owned “This server needs an update” page. Its update press uses the server API.

Screen trust stays with the selected server and its connection, as for the owner’s record and API access. A signed manifest is a later step if the native bridge gains more power. No release on GitHub is needed for screen delivery.

## The server address

First launch asks where the server lives: on this Mac or on another machine. The app never connects to a local server without that choice. The local path offers the default loopback address. The remote path asks for a Tailscale HTTPS address. Both paths offer an existing server or the complete installation and first-setup guide, which ships offline from `ops/mac-home/installation.md`. The existing installer runs separately on the chosen machine. The app does not install or run a server.

The app checks the address before it stores it. HTTP works only on loopback. Remote addresses require HTTPS. An address cannot contain credentials, a path, a query, or a fragment. "Change Server…" opens the same flow, with an option to return to the current server.

Setup uses the same Svelte build at `panels://setup`, a separate origin with no forwarding of `/api` or `/files`. Its entry does not start the workspace or server queries. Only this origin can change the server through the bridge. When the workspace cannot reach the server, it offers the saved address, retry, a link to setup, and access to the last session. The existing change stream restores fresh data after recovery.

## The last session

The app opens with its last state. The native side remembers the screen the window was on and reopens there. The screens keep their server reads and the last navigation view in their own IndexedDB, so the next launch shows them at once, before the server answers. Every restored read then refreshes from the server. Until fresh data replaces the saved copy on screen, the shell status says "Showing your last session", or that navigation is reconnecting. Writes still go to the server, so nothing changes while it cannot be reached.

A saved copy from another build of the screens is not used, because response shapes can change. When the person changes the server, the native side deletes the screens' IndexedDB and forgets the last screen, so one server's Tickets never show under another. That also drops any queued outgoing messages, which belong to the old server. The browser version does none of this.

## Mac behavior

The app has the standard Mac menus. The window opens where it was last left, at the same size. Closing the window does not quit the app; a click on its Dock icon brings the window back.

The window is never narrower than 900px, so the phone layout never appears in it. On macOS it has no system title bar. Its one top row, 52px tall with no fill, is where it is dragged: macOS draws the traffic lights there, then comes a button that hides and shows the sidebar (also ⌃⌘S; it is not kept across a restart). While the sidebar is hidden, the button shows the Review count. Each page's own top row (where it lives, and its actions) is pinned on the same line and stays while the page scrolls. A row too narrow for its actions shortens its context line first, then folds its last actions into a "•••" menu. The sidebar and the pages scroll under the row, behind a short fade. The preload marks the page `data-desktop-window`, and marks whether the window is inactive or in full screen; the screens draw all of this only when that mark is there, so a browser shows them as before. Other systems keep their own title bar, and the preload does not mark the page there.

## App updates

The native side uses electron-updater to download the newest stable desktop release quietly. It selects desktop release tags explicitly, so a server release cannot become an app update. A ready update installs when the app quits, or after the app remains without a window. A reopened window cancels that delay. A failed check or download leaves the installed app usable.

The main process owns the restart dialog when new screens require a newer shell. New screens wait until the installed bridge meets their requirement. The dialog offers a restart only after a verified update meets that requirement. It adds no bridge action for artifact pages.

The desktop workflow signs and notarizes the app independently of server publication. A release to main publishes its ZIP and matching updater metadata on GitHub Releases. Manual runs produce two signed builds for a private feed and publish no release. See the [private update proof](../ops/desktop/private-update-proof.md).

## Notifications

The native side holds a live stream to the server, `/api/notifications/desktop`, while the app runs, also with its window closed. Each event on it is one notification that the server's [notification rules](notifications.md) allowed, with the same words as on the other devices. The native side shows it through macOS, and a newer one for the same Ticket, Manager or Chief replaces the older one. A click brings the app forward and opens that place in the window, and opens the window if it was closed. When the app quits, the stream ends and the Mac gets no more notifications. The stream reconnects by itself after the server comes back, and it moves to the new server after "Change Server…". The switches in Settings are the server's, the same for every device. In the app, the device row says that the Mac gets the notifications, and macOS Settings control banners and sounds.

**Known limit: no replay.** The app shows only notifications that arrive while it is connected. A notification sent while the app is closed, the Mac sleeps, or the connection is down does not reach the Mac later. The browser and iPhone still get it.

## Artifacts and the bridge

Artifact HTML runs at the screens' own origin, so a script in an artifact can reach the bridge. The address read is available in the app and setup. All calls that can change the server answer only a top-level page at `panels://setup`. A frame check alone does not protect them: an artifact can call `parent.panels` in the app’s top frame. The separate setup origin protects the calls. Setup serves only bundled files and never serves artifacts. New calls that act must preserve this boundary.

## Where the code lives

`desktop/` holds the app: `src/main.ts` (lifecycle, window, menus, links), `src/app_scheme.ts` (the scheme and the forward), `src/server_address.ts`, `src/last_session.ts`, `src/notifications.ts` (the stream and macOS notifications), `src/updates/` (native updates), `src/screens/` (verified copies and activation), `src/bridge.ts` (the bridge type), `web/src/desktop/` for the setup and connection screens, and `web/src/components/desktopWindow/` with `web/src/lib/desktopWindow/` for the window's top row. The screens' side of the last session is `web/src/lib/savedSession.ts`. Its build packs the `web/` build. The checks in `tests/e2e/desktop/` drive the built app with Playwright under a virtual display against a test server, through `tests/e2e/desktop.config.ts`. The "Desktop app" workflow runs them.

_Last verified: 2026-11-23._
