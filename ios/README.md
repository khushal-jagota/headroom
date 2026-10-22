# Panels for iOS

Native SwiftUI foundation, iOS 18 minimum. The app defaults to
`https://panels-vps.tail687c57.ts.net`; More → Settings changes and persists the
HTTPS server address. The Mac must be able to reach the server over Tailscale.
No backend changes or third-party packages are needed.

Workspace is the initial tab. Review and Workspace each own their navigation stack. More opens a native sheet
with its own navigation stack, grouped into Work and System. Choosing a destination
expands the sheet; dismissing returns to the previous tab. Navigation uses `AppRoute`; every destination has a separate source file.
The existing full ticket API supplies ticket detail. On 2026-10-22, the lighter
`/api/tickets/{id}/workspace` endpoint returned HTTP 500 for the sampled tickets;
`/api/tickets?detail=full&id=…` succeeded. This adapter deliberately uses that
working endpoint rather than inventing a new backend route.

## Build and run headless

Use Xcode 16 or newer with an installed iOS 18+ simulator runtime. The committed
Xcode project builds without XcodeGen. If adding source files, install XcodeGen
(`brew install xcodegen`) and run `xcodegen generate --spec ios/project.yml`.
Run these commands from the repository root:

```sh
PANELS_DEVICE=5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6
PANELS_PROOF=/Users/khushaljagota/Coding/firstmate/data/pios-foundation
xcrun simctl bootstatus "$PANELS_DEVICE" -b
xcodebuild -project ios/Panels.xcodeproj -scheme Panels \
  -configuration Debug -sdk iphonesimulator \
  -destination "platform=iOS Simulator,id=$PANELS_DEVICE" \
  -derivedDataPath "$PANELS_PROOF/DerivedData" clean build
xcrun simctl install "$PANELS_DEVICE" "$PANELS_PROOF/DerivedData/Build/Products/Debug-iphonesimulator/Panels.app"
xcrun simctl launch "$PANELS_DEVICE" app.panels.ios
xcrun simctl io "$PANELS_DEVICE" screenshot "$PANELS_PROOF/workspace.png"
xcrun simctl io "$PANELS_DEVICE" recordVideo "$PANELS_PROOF/walkthrough.mp4"
# Stop recordVideo with Ctrl-C, which finalizes the video.
xcrun simctl terminate "$PANELS_DEVICE" app.panels.ios
```

Do not open Simulator.app or take over the user's screen. If the assigned simulator
is shut down, run `xcrun simctl boot "$PANELS_DEVICE"` before `bootstatus`.

Exactly three shared lanes were created, all iPhone 17, iOS 27.0 (the installed
runtime; the deployment target remains iOS 18):

| Lane | Device identifier |
| --- | --- |
| PanelsLaneA | `5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6` |
| PanelsLaneB | `566D051B-B6BB-4046-9038-66C05CCC2B96` |
| PanelsLaneC | `5D323346-68F9-4A0D-A90B-74507469C98A` |

Later workers reuse their assigned lane. Erase between runs: terminate your app,
shut down **only your assigned device**, run `xcrun simctl erase "$PANELS_DEVICE"`,
then boot it headless again. Never create or delete devices per run, never use the
ambiguous `booted` selector, and never touch another worker's lane. The foundation
uses Lane A and leaves all three lanes available for the parallel screen workers.

## Small verification target

```sh
xcodebuild -project ios/Panels.xcodeproj -scheme Panels \
  -destination "platform=iOS Simulator,id=$PANELS_DEVICE" \
  -derivedDataPath "$PANELS_PROOF/DerivedData" \
  -parallel-testing-enabled NO test
```

There are no UI test suites. `PanelsTests/Fixtures` contains unmodified responses
captured with read-only GETs on 2026-10-22. Tests protect two previously uncovered
wire-contract risks: snake-case/nullable/grouping and page decoding; and the
review/detail/artifact response shapes. One tiny parser test protects heartbeat
comments being mistaken for changes. Setup is one fixture loader, with no backend
or browser host. Simulator launch and screenshot are a separate smoke check; they
do not prove physical-device haptics, frame rate, or every navigation flow.

## Services and ownership

| Folder | Owner / integration surface |
| --- | --- |
| `Panels/App` | Shared shell and `AppRoute`; coordinate additions with integration owner |
| `Panels/Core/Networking` | Shared `APIClient`, GET endpoints, SSE change stream |
| `Panels/Core/State` | Shared observable `AppStore` and foreground refresh lifecycle |
| `Panels/Core/Theme` | Shared web-derived tokens, Dynamic Type, `Haptics` |
| `Panels/Core/Components` | Shared deployment strip, retry state, `ConversationPlaceholder` slot |
| `Panels/Models` | Server DTOs split by domain; extend the relevant domain file |
| `Panels/Features/Review` | Review worker |
| `Panels/Features/Workspace` | Workspace home worker |
| `Panels/Features/Ticket` | Ticket worker |
| `Panels/Features/Orchestrator` | Orchestrator worker (server calls it manager) |
| `Panels/Features/Chief` | Chief of Staff worker |
| `Panels/Features/Artifacts` | Artifact list and viewer worker |
| `Panels/Features/More` | More worker; separate Ideas, Feedback, Usage, VPS, Settings folders |
| `PanelsTests` | Focused contract verification; coordinate shared fixture changes |
| `project.yml`, `Panels.xcodeproj` | Integration owner; regenerate once after combining added files |

Inject `AppStore` through SwiftUI's environment. Use `store.client` for detail
reads. The shell owns the sole `/api/changes` connection, cancels it in background,
reconnects after disconnect, and coalesces pending refresh signals into one slot.
`store.revision` lets mounted detail screens refresh after an overview update.
The app uses the server's general change stream and REST reads, not a duplicate of
the web navigation replica protocol. Overview reads run concurrently and retain
previous data with an explicit error on failure. Active manager and ticket lists
use bounded pages; Load more preserves the loaded window across refreshes. A
manager's detail supplies its complete grouped tickets. Workspace shows Chief tickets and the loaded unsettled orchestrators. Tickets whose
parent is settled stay inside Settled; loading more orchestrators reveals their
children without inventing a second active card for a settled parent.
Artifact directories refresh on selection and pull-to-refresh because filesystem
writes need not emit a database change.

`ConversationPlaceholder` is the clearly named integration slot on Chief,
Orchestrator and Ticket. No transcript, composer, read marker, or conversation
layout has been implemented; the agreed conversation design comes later. Artifact viewing uses native file screens. Usage, VPS and Settings have native read-only
screens. Feedback implements the Open/Handled inbox, grouped Ticket links, page
context links and Dismiss/Reopen actions through its API adapter. It follows
foreground change revisions and supports pull-to-refresh. Its empty state omits
the web's capture button instruction because native global feedback capture is
not yet implemented. Feedback writes are exercised only with an injected stub
or URLProtocol interception.

Ideas reads the live idea list and project catalogue, expands captured detail,
and captures a title with optional detail and project assignment. Return in the
title field captures; project chips assign the new idea, matching the web. Failed
captures keep the draft. The capture POST lives behind `IdeasServing`; its tests
use a stub transport, and simulator verification never submits a live capture.

Orchestrator and Chief pages show expandable recaps and grouped workers, with
working and unread marks from the server. Active groups start open; Backlogged
and Done start folded. Chief loads its own paginated workers and retains loaded
pages after live updates. Both pages condense the title into the navigation bar
on scroll and honor Reduce Motion.

The page menu holds Ask for a recap and Copy link; orchestrators also offer
Settle or Reopen. Recaps show up to eight lines and offer Copy/Edit on long press.
Goal opens a full reading page with an editor. Worker long presses offer a recap
preview, Open, Ask for a recap, and Backlog or Resume. Folded groups show their
loaded unread count. Group folds are silent; confirmed writes give success haptics.

Ask for a recap uses the existing recap-request endpoint through the API adapter.
It sends only after an explicit tap, prevents overlapping requests, and displays
refused or uncertain delivery without claiming success. Simulator verification
uses read-only navigation; recap requests are verified with an in-process mocked
URL session and never sent to the live server during tests. Goal/recap saves,
settling and backlog changes also have mocked request-contract coverage.

The ticket page adds API actions for recap requests, backlog/resume, approval
limits, proposal acceptance/return, owner completion, field/title/recap editing,
blocker removal and pristine worker setup. Live simulator verification is read-only; ticket and Feedback writes are
exercised only with an injected stub or URLProtocol interception.



Review supports approval and send-back through its API adapter; see
`Review.md` for its behavior and focused verification. Live-server simulator
verification remains read-only; mutation verification uses a mocked transport.

Haptic helpers offer light, medium, success, error and selection. Navigation and tab
changes use them, as does saving Settings. Native navigation transitions supply
motion, respect system accessibility preferences, and avoid custom bouncing.

## Feedback verification

`PanelsTests/FeedbackTests` protects four distinct gaps in the foundation tests:
Feedback response grouping and attention precedence, safe native page-context
routing, retaining notes after a failed mutation and refreshing after success,
and the POST adapter's encoding of one identifier component. Tests reuse the
shared decoder, one small synthetic response, an actor stub and an ephemeral
URLSession; they start no backend or browser host. Feedback's views, contracts,
presentation and API extension are confined to `Features/More/Feedback`.

## Usage, VPS and Settings

Usage reads cached Codex and Claude allowances, plus recorded local CLI token and
cost totals for today, 7, 30 or 90 days. Provider filters apply to the totals and
chart. Missing values stay unavailable and partial source reads show their issues.
Pull-to-refresh rereads the stored snapshot; it does not ingest CLI files, refresh
provider credentials, sign in or update a backend. Cost estimates are not bills.

VPS reads machine measurements, CPU history, services and paged processes. Its
15-second foreground poll preserves the loaded process window. CPU history offers
5 minutes, 24 hours and 30 days; points do not connect across missing samples.
The app never renews the server's activity lease. Stale readings keep their sample
time and show “No feed”. Pull-to-refresh also reloads history.

Settings reads agent launch defaults, role skills, schedules and notification
preferences. Server preferences are read-only; management remains on the web.
The Server sheet changes only this app's stored HTTPS address. Its save closure
can be replaced by a stub. Native push is not connected in this preview.

The four system-screen tests cover gaps not covered by the foundation fixtures:
nullable recorded cost versus a real zero; per-core CPU conversion and missing
memory; fractional allowance timestamps and an absent feed; and unsafe server
addresses. They reuse the API decoder and have no connected test host. Run them
on macOS without a simulator:

```sh
ios/scripts/test-system-models.sh
```

For this delivery, firstmate explicitly deferred all simulator testing and Gemini
motion review. Verification is a generic iOS Simulator SDK compile, these host
unit tests, and decoding eight read-only endpoint snapshots. No iOS interaction,
motion, or physical haptic behavior is claimed as verified.

## Ideas verification

`IdeasTests` covers three distinct gaps: blank submissions and failed writes must
preserve a draft without accidental requests; acknowledged captures must display
the server record and reset their draft; and the API adapter must send the exact
POST payload (`project_id`, omitted blank optional fields) and decode the response.
These are fast unit tests using an actor stub and URLProtocol, with no backend or
browser host. The read-only simulator walkthrough is an external capture harness,
not an added UI suite. Haptics still need physical-device verification.

After Feedback and the system screens were integrated, Ideas was rebased while
preserving every More destination. Firstmate requested compile-only verification
and the existing host tests for that integration; no simulator was used for the
rebase checks.
## Workspace home

Workspace follows the web rail's section precedence and visibility: approval,
answer, Yours, errors, Agent, Consequences, agent approval, then Other. Backlogged,
blocked and completed tickets leave this rail. Unread dots and working spinners
are independent. Chief activity comes from the read-only workers index.

The approved mobile design keeps hairline cards, title-case group labels, one-line
rows and native pushes. Settled is a plain expandable list after the cards, with
the server's total count. Pages load in batches of 30; refresh preserves the loaded
window. Surviving rows retain order, and section changes fade instead of travelling.
Regrouping waits during scrolling and context previews; titles and marks stay live.
Reduce Motion removes layout animation.

Long press previews the full title, recap and manifest-derived stage sentence.
Its menu opens the page, requests a recap, backlogs/resumes a ticket, or settles an
orchestrator. These use existing endpoints. Failed or uncertain actions show an
error; confirmed actions give success haptics. Pushes and the Settled toggle are
silent, and pull refresh/context menus use system feedback. Every shared route
destination hides the tab bar; returning to a tab root restores it. Physical haptic feel
still needs a device. Conversation contents remain the shared placeholder.

Four focused Workspace tests reuse the existing ticket fixture and an in-process
URLProtocol stub. They cover attention precedence, hidden-ticket exclusion, stable
section ordering, and write methods/payloads including uncertain recap delivery.
They add no backend or browser host. Live Simulator verification invokes no writes.

## Artifact integration

Ticket and Orchestrator screens open `AppRoute.artifacts(owner:directory:)`, with
an empty directory for the root. `ArtifactListView(owner:directory:)` is also
available directly inside a navigation stack with the app's route destinations.
`ArtifactsRow(owner:count:)` supplies the approved 50pt entry row with count and
no chevron; it is absent at zero. Page owners supply the catalog count.
`AppRoute.artifact(owner:path:title:)` opens a specific managed file. Catalog links
retain their actual owner, including links to files on another ticket.

The list reads the selected directory on arrival and pull-to-refresh. HTML runs
inside a sandboxed frame in a nonpersistent WKWebView, with relative file assets
and native navigation for managed links. Folder sections show five files before
expanding, with image thumbnails and Open/Share/Copy link context menus.
Markdown uses native text, and images support pinch, pan and double-tap zoom.
PDF uses continuous PDFKit scrolling, supported media uses AVKit, and other
supported files use Quick Look. Text wraps and previews at most 256 KB.
Sharing sends the downloaded file through the system share sheet. Temporary files
are removed when their preview and share sheet release them. Files too large for
the HTML/markdown preview remain available to Quick Look or sharing. Copy link
copies the web preview address. Artifact pushes and zooms are silent; Copy link
uses success feedback and reload errors use error feedback. The shared shell
retains ownership of global navigation feedback and deployment chrome. The
viewer reserves the existing conversation placeholder for non-image files;
callers opening from Review can set `showsConversationPlaceholder: false`. No
conversation state or composer is created. The app never writes
to the server when listing, opening, reloading or sharing artifacts.

The three artifact logic tests cover cross-owner URLs and encoding, unsafe file
paths, and preservation of markdown code blocks. They reuse the existing XCTest
target and require no server. Simulator captures are a separate read-only smoke
pass, and do not establish physical-device haptics or frame-rate performance.
