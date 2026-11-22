# Panels for iOS

Native SwiftUI foundation, iOS 18 minimum. The app defaults to
`https://panels-vps.tail687c57.ts.net`; Settings (the gear in the Sidebar) changes and persists the
HTTPS server address. The Mac must be able to reach the server over Tailscale.
No backend changes or third-party packages are needed.

The app icon uses the Chief of Staff portrait from
`web/src/assets/chief-of-staff-profile.webp`, centered on its black background.
Its opaque 1024×1024 PNG lives in `Panels/Assets.xcassets/AppIcon.appiconset`;
`project.yml` selects `AppIcon` for the Panels target.

Review is the root at launch and opens on its summary. There is no tab bar. The destinations are Review,
Chief, each Manager and Settings. Only the current destination is on screen and
mounted, in its own navigation stack. The shell keeps each destination's page path,
so a return shows the page Khushal left, for example after a notification moved him
elsewhere. Drafts survive every switch because they are saved per owner and reload
when a page mounts. Scroll position and a view's in-memory state do not.

A slide-over Sidebar replaces the tab bar; see [Sidebar](#sidebar). Every page uses
the native navigation bar: the Sidebar button on a root page, the system back button
and edge swipe on a pushed page. Page menus are bar items. The shared `AppRoute`
describes every pushed page, and every destination has a separate source file.

The existing full ticket API supplies ticket detail. On 2026-10-22, the lighter
`/api/tickets/{id}/workspace` endpoint returned HTTP 500 for the sampled tickets;
`/api/tickets?detail=full&id=…` succeeded. This adapter deliberately uses that
working endpoint rather than inventing a new backend route. Its saved `workflow`
supplies Ticket and Review controls, stage names and approval gates. The nullable
`worker_type` identifies the creation preset and never supplies those controls.

## Refresh work

While the app is in the foreground it follows one navigation view stream for
what is on screen, as desktop does: a snapshot first, then patches. Review is
always in that scope, for its badge, and so is the Manager list the Sidebar shows.
A detail page adds its own record (and, for Chief and orchestrators, a children
window). A new demand opens a stream for the new scope beside the current one, as
the web does. The current stream keeps the view live until the new snapshot lands,
and then it closes, so two streams are open only during that wait. A demand the
running stream already carries, as for a Ticket opened from Review, waits for the
next stream. Leaving a screen only narrows the next stream. Review's skipped
entries ride the next stream; its return order opens one at once. One coherent view replaces the
earlier Review, manager and ticket list reads; the phone no longer reads
`/api/review`. A launch shows the last saved view at once, if it is less than seven
days old. A pull on Review opens a fresh stream the same way, and the
pull ends when its snapshot lands. A return from the background, a dropped connection,
a gap, a reset or a closed stream all start a
fresh stream, whose first frame is a fresh snapshot. A brief inactive moment, such as
Control Center or a notification pull-down, keeps the streams. A stream with no bytes
for 35 seconds fails; the server's heartbeat every 15 seconds keeps a good one open.
A stream that was current
reconnects after one second, as the web does; failures retry after 1, 2, 4 and
then 8 seconds and show Reconnecting. Then saved work drains, as before.

Ordinary responses have one shared owner per server and exact resource key. Mounted
screens acquire handles for their documents. A payload-free change signal probes
active keys and reads changed bodies. Each body carries its own epoch and revision.
Generation checks reject late responses after a newer probe or server change.
Explicit refresh and confirmed writes reconcile affected reads immediately.
Typed decoding and response-cache reads run off the main actor. Handles reuse
accepted decoded values, and identical response bytes do not republish content.
Foreground return and reconnect reconcile active demand. Domain deadlines cover
calendar changes without a database write.

Goals, their readings, holder pages, and catalogs use this owner. Local edits and
manual commands remain separate from accepted responses. The app session owns unfinished
Goal and holder edits across route changes until Save or explicit discard. Unsaved
conflict drafts do not survive app termination.
Charts keep previous accepted readings, marked stale, while a newer projection loads.
Goal details use the
addressed document revision; lists retain their shared revision. Readings also
retain shared measure and calendar dependencies, including datasets and formulas.
Ticket, Chief, Manager, settings, and deployment details use domain registrations.
Unversioned reads use their scoped navigation stamps where available. Volatile
unversioned reads retain a bounded signal fallback. Failed automatic reads back off;
explicit retry remains immediate. Artifact inventory still reads
on selection; previews retain Reload. Metrics retain their foreground sampling.

Inactive resource responses have count, byte, and age bounds. The serial cache
writer coalesces bounded replacement work. Live publication does not wait for disk.
Restored responses remain stale. Cache errors do not clear durable user-work errors.
Drafts and commands stay outside this replaceable queue.

Navigation and conversation owners checkpoint admitted display at five-second
intervals during updates and on background entry. Checkpoint encoding runs off the
main actor. Restored display stays stale and never supplies
stream coverage or read credit. Conversation display retains at most 300 items
and 512 KiB of inline and expanded bodies. Visible rows keep their neighborhood.
Older paging remains available; Latest returns to the current edge after trimming.
Collapsed conversations request status without transcript content; their preview
follows those compact facts while preserving the latest unsent local message.

The view code lives in `Core/Navigation`. The wire subset is hand-written Codable,
pinned to projection 7 and wire 3, and ignores fields the server adds. Ticket,
orchestrator and Review facts are derived from view records the way desktop's
`presentation.ts` derives them, then mapped into the existing native models.
Frame application (snapshot, checkpoint, patch, reset; a gap needs a fresh
snapshot) is a pure unit that each stream applies to its own snapshot.
The server bounds a view to 256 rows and 60 per window. Mounted screens share
that budget the way the web owner shares it. Orchestrator and Chief pages acquire shared
resource handles for their goals, recaps, and detail documents. The Sidebar's
Settled group keeps its paged list read, and reads only while expanded; its count
comes from the view.

The full conversation acknowledges remote rows only after they enter its visible
scroll area. Polls, stream progress, the dock, hidden screens and inactive scenes
earn no read credit. A deferred message earns credit only after its body is loaded
and visible. The read point for that row's exact conversation and sequence moves
on screen at once, and the outbox carries it to the server. Read positions never
move backwards when an older configuration arrives.
Overlapping refresh requests share a following read instead of being dropped.
The transcript projection is retained until its message, expanded body, outgoing
receipt, live text or ordering changes. An unchanged live-output reference reuses
its text; a changed byte count, version or body identity reads it again. Image
artifacts load their image once per successful document load, outside the view
body. A failed reload retains the previous document and image.

## Photo messages

The native composer prepares each photo off the main actor when it joins the draft.
Its attachment shows “Preparing photo…” and Send stays disabled until preparation finishes.
Accepted photos reuse their prepared bytes; only a combined size above 3 MiB triggers another reduction.
Restored raw drafts also prepare off the main actor before send. Newer draft edits cancel that send capture.
HEIC becomes
an oriented JPEG. Large images are recompressed or reduced to keep all image bytes
within the server's existing 3 MiB message limit. Supported photos that already fit
retain their bytes. File attachments keep their existing handling.

The same preparation applies to restored editable drafts, Review replies and explicit
retries of failed messages. It never changes a saved outgoing body.
If the server still returns HTTP 422, the phone shows a photo-specific error and keeps
the draft. An ordinary conversation restores its cleared draft only when both saved
composer versions still match the cleared versions. Newer edits stay intact. An explicit
retry gets a new message identity and clears only the exact restored draft it retries.
The server's limits and retention policy do not change.

## Typography

Shared text roles in `Core/Theme/Theme.swift` follow the mobile web's weights.
Review and Ticket titles use the approved web base of 26 points and semibold (600).
Completion text remains medium. Proposal decisions use medium, while message actions and
message prose remain regular. Notification labels use medium. Ideas capture and configuration headings use medium;
artifact filenames remain regular. Section headings follow their web surface:
regular for navigation and VPS, medium for configuration and Usage.

`panelsNavigationTitle` sets the weight on each native navigation item. Usage
keeps its semibold heading; other utility pages use medium. This retains native
navigation geometry and restores the destination's style on back navigation.
The Sidebar, proposals, Ticket and all Markdown use web base sizes with Dynamic Type
scaling. Other font roles remain native. Markdown text is 14 points, headings 15 points
with bold h1/h4–h6 and semibold h2/h3, and code 12.5 points.

## Proposal decisions

Review and Ticket share a read-only proposal card, with the stage on the left and
the reviewer on the right, and a docked note with Return and Approve in one
equal-width row. Review adds Skip to that row and keeps dictation; its Open ticket
sits in Review's top bar. An empty Return note focuses the field. Approval captures the displayed note
for that exact server and proposal. Notes retain the existing durable draft keys.
Approve opens a standard grouped Form sheet. Approved until lists only the stages
after the waiting one, and Then reviews is a segmented control. Done is listed
when the Worker type's ceiling range includes it, as on the web. When the next
stage is Done, Approve takes one tap. The approver
in the card opens a Reviewer sheet with one tap to choose. Both sheets are shared by
Review and the Ticket page (`DecisionSheets.swift`) and wait for the server inside
the sheet. A failure shows inline. A reviewer change uses the existing PATCH route.
It never enters the Outbox and has no operation receipt. The approval limit of a
Ticket with no proposal waiting changes in its own sheet. Approve and Return retain protected operations and
saved-work recovery. Proposal text, approved steps, and Ticket titles have no phone
edit controls.

## Build and run headless

Use Xcode 16 or newer with an installed iOS 18+ simulator runtime. The committed
Xcode project builds without XcodeGen. If adding source files, install XcodeGen
(`brew install xcodegen`) and run `ios/scripts/generate-project.sh`.
Run these commands from the repository root:

```sh
PANELS_DEVICE=5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6
PANELS_PROOF=/path/to/proof-directory
xcrun simctl bootstatus "$PANELS_DEVICE" -b
xcodebuild -project ios/Panels.xcodeproj -scheme Panels \
  -configuration Debug -sdk iphonesimulator \
  -destination "platform=iOS Simulator,id=$PANELS_DEVICE" \
  -derivedDataPath "$PANELS_PROOF/DerivedData" clean build
xcrun simctl install "$PANELS_DEVICE" "$PANELS_PROOF/DerivedData/Build/Products/Debug-iphonesimulator/Panels.app"
xcrun simctl launch "$PANELS_DEVICE" com.khushaljagota.panels
xcrun simctl io "$PANELS_DEVICE" screenshot "$PANELS_PROOF/review.png"
xcrun simctl io "$PANELS_DEVICE" recordVideo "$PANELS_PROOF/walkthrough.mp4"
# Stop recordVideo with Ctrl-C, which finalizes the video.
xcrun simctl terminate "$PANELS_DEVICE" com.khushaljagota.panels
```

Do not open Simulator.app or take over the user's screen. If the assigned simulator
is shut down, run `xcrun simctl boot "$PANELS_DEVICE"` before `bootstatus`.

Exactly three shared lanes were created, all iPhone 17, iOS 27.0 (the installed
runtime; the deployment target remains iOS 18):

| Lane | Device identifier |
| --- | --- |
| PanelsLaneA | `5BEBCEA8-BDF2-4F47-9CAA-0E25C1D147B6` |
| PanelsLaneB | `47890EEA-D1D8-4E5A-AF74-DB66866D7840` |
| PanelsLaneC | `5D323346-68F9-4A0D-A90B-74507469C98A` |

Later workers reuse their assigned lane. Erase between runs: terminate your app,
shut down **only your assigned device**, run `xcrun simctl erase "$PANELS_DEVICE"`,
then boot it headless again. Never create or delete devices per run, never use the
ambiguous `booted` selector, and never touch another worker's lane. The foundation
uses Lane A and leaves all three lanes available for the parallel screen workers.

## TestFlight signing

Panels uses Blue Door Labs team `FB99BBDJBA` and bundle ID
`com.khushaljagota.panels`. Version 0.1 started at build 1. Release builds for
physical iOS use automatic signing and the Push Notifications capability, with
`aps-environment=production`. Push registration remains disabled. The app declares all four interface
orientations for its existing iPhone and iPad device families.

Debug and simulator builds remain unsigned. Generic iOS compile checks can
continue to pass `CODE_SIGNING_ALLOWED=NO` explicitly. Generate the project after
changing signing settings in `project.yml`, using
`ios/scripts/generate-project.sh`. The wrapper corrects XcodeGen 2.46’s nested
capability serialization bug so the committed Push capability remains a dictionary.

Archive with the authorized App Store Connect API key and
`-allowProvisioningUpdates`; pass `-authenticationKeyPath`,
`-authenticationKeyID` and `-authenticationKeyIssuerID` from private inputs.
Export with automatic signing and `method=app-store-connect`. Before upload,
inspect both the archive and exported IPA with `codesign -d --entitlements :-`:
the distribution app and embedded profile must contain production APNs and the
expected application/team identifiers. Verify the exported signature and version
and build number, then upload. App Store Connect processing and internal tester access
must be confirmed separately; signing configuration alone does not establish
TestFlight availability.

## Small verification target

```sh
xcodebuild -project ios/Panels.xcodeproj -scheme Panels \
  -destination "platform=iOS Simulator,id=$PANELS_DEVICE" \
  -derivedDataPath "$PANELS_PROOF/DerivedData" \
  -parallel-testing-enabled NO test
```

## Permanent screen tests and one-off reviews

A permanent screen test runs on every release. Admit it only for a core journey
that a real screen must prove. Keep one or two tests per journey. Merge overlapping
walks into one walk. Use host or model tests for logic, state, wording, and layout.
Use one-off Simulator reviews for appearance and visual polish.

`PanelsNotifications` runs `PanelsTests` and the five permanent methods in
`PanelsNotificationUITests`. `Panels` runs the unit target. The permanent list is:

| Method | Core journey |
| --- | --- |
| `ConversationOperationTests/testOperationsAgainstLocalStub` | Open a conversation, read, reply, and paste and send one image. |
| `ReviewMessagesTests/testUnreadReplyRefusalQueueAndHistoricalNavigation` | Reach Chief and Review through the Sidebar, reply, and follow the conversation route. |
| `ReviewMessagesTests/testSplitCardsSkipAndReturn` | Act on a Review item and return to skipped work. |
| `OfflineOperationTests/testEveryActionLandsOnceAcrossDroppedConnectionsAndLostResponses` | Send, approve, send back and mark read through dropped connections and lost replies; each lands once with no cover or status note. Try Again resends a refused message once. |
| `ServerReachabilityTests/testUnreachableServerShowsNoCoverAndRecovers` | Lose the server: no cover appears, the app stays usable, and Review opens again without a relaunch. |

Discover the native tests before a release run. Discovery must find all five
methods. A selection that executes zero tests fails, even if Xcode returns success.

`PanelsReviewUITests` and the separate `PanelsReview` scheme contain one-off walks.
They stay outside the permanent release suite. The existing fixture harnesses select
that scheme with `--review`. These walks cover specialist attachment previews,
Latest scrolling, detail navigation, large text, Goal edits, notification routing,
Review composer appearance, the shell (`SidebarNavigationTests/testDestinationsBackSwipeAndOneComposer`:
Sidebar cards, destination switches, Back swipe over a root page, edge-only Sidebar
swipe, one composer under an open conversation, and a Chief draft kept across
destinations), and a live read-only review. The live walk
contains historical selectors; review its comments before use. A one-off walk is a
review tool, not a permanent gate. Record its result and diagnose failures.

A route without a screen test still needs a one-off check when a change touches it.
Keep physical-device gaps on the phone checklist, including server entry and Save,
airplane-mode delivery, Goal edit and Save, and notification taps.

A flaky test is a defect in the test or the product. Find and fix its cause.
Do not use retries, longer timeouts, or load allowances to obtain a pass.
After a tap, wait for its result with `waitForExistence` or
`waitForNonExistence`. Share the transition deadline across related assertions.
Bind each row by its own stable identifier. Do not bind a changing list through
`firstMatch` or a row position.

`PanelsTests/Fixtures` contains response samples from read-only GETs on
2026-10-22. Host tests cover wire contracts and model behavior without a browser
or backend host. Simulator results do not prove physical-device haptics, frame
rate, delivery through APNs, or every navigation route.

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
| `Panels/Features/Sidebar` | Sidebar |
| `Panels/Features/Ticket` | Ticket worker |
| `Panels/Features/Orchestrator` | Orchestrator worker (server calls it manager) |
| `Panels/Features/Chief` | Chief of Staff worker |
| `Panels/Features/Artifacts` | Artifact list and viewer worker |
| `Panels/Features/More` | Settings, Ideas, Usage, VPS and the Settings places section |
| `PanelsTests` | Focused contract verification; coordinate shared fixture changes |
| `project.yml`, `Panels.xcodeproj` | Integration owner; regenerate once after combining added files |

Inject `AppStore` through SwiftUI's environment. Use `store.client` for detail
reads. The shell owns `store.run()`, keyed on `AppStore.follows(scenePhase)`. It follows the view stream and the change
stream, cancels both in background and reconnects after disconnect. Pull to refresh on
view-backed screens calls `store.pull()`; refreshes after confirmed writes call `store.refresh()`. Screens state
their windows with `store.setNavigationDemand`; detail pages use `.navigationDemand`
and key reloads with `.reload(onChangeOf:)` on `store.ticketStamp`,
`managerStamp` or `chiefStamp`. Overview reads run
concurrently and retain previous data with an explicit error on failure. The Sidebar
lists the active Managers from one window. A manager's detail
supplies its complete grouped tickets. Tickets whose parent is settled stay inside
Settled, so a settled parent never gets a second active card.
Artifact directories refresh on selection and pull-to-refresh because filesystem
writes need not emit a database change.

`ConversationNavigation` supplies the shared composer and conversation page on Chief,
Orchestrator and Ticket, described below. Artifact viewing uses native file screens. Usage, VPS and Settings have native read-only
screens.

Ideas reads the live idea list and project catalogue, expands captured detail,
and captures a title with optional detail and project assignment. Return in the
title field captures; project chips assign the new idea, matching the web. Failed
captures keep the draft. The capture POST lives behind `IdeasServing`; its tests
use a stub transport, and simulator verification never submits a live capture.

Orchestrator and Chief pages show expandable recaps and grouped workers, with
working and unread marks from the server. Active groups start open; Backlogged
and Done start folded. Chief loads its own paginated workers and retains loaded
pages after live updates. The native bar carries no title.

The actions button in the bar opens a grouped Form sheet with Ask for a recap and
Copy link. Orchestrators also offer Settle or Reopen. Recaps show up to eight lines.
Folded groups show their loaded unread count. Group folds are silent; confirmed writes give success haptics.

Ask for a recap uses the existing recap-request endpoint through the API adapter.
It sends only after an explicit tap and waits inside the sheet, as the Ticket
actions do. A refused delivery shows inline in the sheet. An uncertain delivery shows
no connection note. Simulator verification
uses read-only navigation; recap requests are verified with an in-process mocked
URL session and never sent to the live server during tests. Settling also has
mocked request-contract coverage.

The ticket page adds API actions for recap requests, backlog/resume, approval
limits, proposal acceptance/return and pristine worker setup. Live simulator
verification is read-only; ticket writes are exercised only with an injected stub or
URLProtocol interception.

Review supports approval and send-back through its API adapter; see
`Review.md` for its behavior and focused verification. Live-server simulator
verification remains read-only; mutation verification uses a mocked transport.

Haptic helpers offer light, success, error and selection. By the owner’s
2026-10-22 override, a destination change gives selection feedback and shell push/back changes
give a light impact. Disclosures and read retries
are silent; pull refresh and menus retain system feedback. Selection also
acknowledges a changed control value.
Ideas uses light feedback when submitting and success or error on
the result; page menu actions acknowledge the result only. Copy and
local Settings saves acknowledge completion. Background updates never buzz.
Native pushes retain system motion; shared disclosures use the same bounce-free
spring, with instant changes under Reduce Motion.

## Usage, VPS and Settings

Usage reads cached allowances for the backends the server says report them, plus recorded local CLI token and
cost totals for today, 7, 30 or 90 days. Provider filters apply to the totals and
chart. Missing values stay unavailable and partial source reads show their issues.
Pull-to-refresh and the toolbar refresh button request fresh provider allowances
and reread local CLI records, just like Usage on the web. Existing readings stay
visible during refresh, with each source’s last-read time; failed provider reads
keep the previous allowance and show the error. Refresh gives haptic feedback.
Opening the screen only reads stored values. Cost estimates are not bills.
The Panels server row at the top of Settings displays deployment status without
opening a page; VPS has its own navigation row.

VPS reads machine measurements, CPU history, services and paged processes. Its
15-second foreground poll preserves the loaded process window. CPU history offers
5 minutes, 24 hours and 30 days; points do not connect across missing samples.
The app never renews the server's activity lease. Stale readings keep their sample
time and show “No feed”. Pull-to-refresh also reloads history.

Settings reads agent launch defaults, role skills, schedules and notification
preferences. Notification switches change the same server preferences as the web.
A failed save retains the confirmed value and offers Retry. Other configuration
remains read-only; management stays on the web.
The Server sheet changes only this app's stored HTTPS address. Its save closure
can be replaced by a stub. Native notification permission, APNs token handling and tap routing are implemented.
Server delivery remains disabled until the APNs endpoint and Apple signing are
configured; see [the notification handoff](NOTIFICATIONS.md).

The four system-screen tests cover gaps not covered by the foundation fixtures:
nullable recorded cost versus a real zero; per-core CPU conversion and missing
memory; fractional allowance timestamps and an absent feed; and unsafe server
addresses. They reuse the API decoder and have no connected test host. Run them
on macOS without a simulator:

```sh
ios/scripts/test-system-models.sh
```

For this delivery, all simulator testing and Gemini motion review were deferred. Verification is a generic iOS Simulator SDK compile, these host
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

## Sidebar

The Sidebar slides over the current page. It opens from the Sidebar button at the
leading edge of a root page's bar, or a drag from the leading screen edge of a root
page. That drag is the system screen-edge pan, so taps and page drags are untouched;
on a pushed page the same edge is the system Back swipe. It closes on a tap on the
scrim, a drag back, or a choice.

Chief comes first, with the eyebrow "All projects". The active Managers follow, each
with its project eyebrow, the worst active goal's status tag ("Off track", "At risk"
or "On track") with a haze behind it, and the conversation signal mark. Settled is a
collapsed group with its count; each row has Reopen, which uses the existing Manager
endpoint. At the bottom, Review with its badge sits on the left and the Settings gear
on the right. Cards carry no ticket rows, goal rows or machine marks. Tickets stay
reachable from the Chief and Manager pages and from Review.

The Manager list is always in the navigation view scope through a permanent demand,
so opening the Sidebar does not change the stream. Expanding Settled adds its window
while it is open. Goal tags come from the goals resource while the Sidebar is open.

A Ticket or Goal link, from a notification, from `/#/review?ticket=…` or from
Review's Open ticket, opens on top of Review, and Back returns to Review. A Chief or
Manager link opens that destination at its root. `/#/review` opens Review at its root.

## Artifact integration

Ticket and Orchestrator pages expand their artifact catalog beneath the Artifacts
row. `ArtifactsRow(owner:count:)` supplies the shared 50pt entry row and count;
it is absent at zero. Chief has no artifact catalog. Folders expand in place too.
`AppRoute.artifact(owner:path:title:dock:)` opens a specific managed file. Catalog links
retain their actual file owner, including links to files on another ticket.

The inline list reads on expansion and when the owner's file count changes, with paging and a search
field for larger catalogs. HTML runs from its Panels URL in a persistent WKWebView with relative file assets and ordinary page navigation. HTML previews offer Phone width and Fit
desktop. Fit desktop scales a 1024-point canvas into the available width; pinch
zoom lets the reader inspect a fixed-width design without changing its styles. Folder sections show five files before
expanding, with image thumbnails.
Markdown uses the shared renderer below, and images support pinch, pan and double-tap zoom.
PDF uses continuous PDFKit scrolling, supported media uses AVKit, and other
supported files use Quick Look. Text wraps and previews at most 256 KB.
Sharing sends the downloaded file through the system share sheet. Temporary files
are removed when their preview and share sheet release them. Files too large for the Markdown or original sandboxed HTML preview remain available to Quick Look or sharing. Copy link
copies the web preview address. Artifact pushes and zooms are silent; Copy link
uses success feedback and reload errors use error feedback. The shared shell
retains ownership of global navigation feedback and deployment chrome. The route's
`ArtifactDock` says what sits below the file, chosen by where it was opened, and
further artifact links keep it. A Ticket, Orchestrator or conversation page docks
the same native conversation as that page, image previews included: the viewer
resolves the current conversation ID from the owner and uses the owner send door. A
full conversation page keeps the model it opened with, so its draft, tail and read
point stay one when an artifact docks a composer for the same owner. A Review card docks its own controls on the card's decision, so the
note or reply is the card's draft: the proposal's note, voice, attachments, Skip,
Return and Approve with its sheet, or the message's `Reply…`, Skip and Mark read, with
any refusal. Only the top viewer docks them. Acting there, or the card leaving the
screen, closes every stacked viewer back to Review, and the queue moves on as if the
card had acted. Catalog files keep the catalog's conversation owner while managed
links retain their actual file owner. Listing, opening, reloading and sharing
artifacts do not write to the server; sending a message requires an explicit tap.

Artifact links in proposals, messages, Ticket fields and Goal prose stay inline
with their authored labels. Only linked photos and videos also display inline.
The shared media selector excludes code, external links and every other file kind,
so Review never reads or expands a text file before a tap. Audio stays a link.
A tap opens the existing native viewer. Conversation text resolves relative links
against the configured server and uses the same managed-file route. Artifact
catalogs and viewers keep their existing presentation.

One renderer, `PanelsMarkdown`, shows Markdown on the artifact page, in proposals,
messages, Ticket fields, Goal prose and conversation rows. Code and tables scroll
sideways inside the page, and text hugs its width, so a short own message keeps a short
bubble. Each view parses its source once. Only the artifact page lays blocks out lazily,
so conversation rows keep stable heights. A closed `panels-chart` fence that passes the
web's checks draws a Swift Charts bar or line chart with the goal number formats. Any
other fence stays code. A fence inside a quote is not read, so a quoted chart, which the
web draws, shows as quote text.

Artifact logic tests cover cross-owner URLs and encoding, unsafe file paths,
Markdown code blocks, chart fences and specs, and the photo/video-only rule. The media cases cover a gap
in the earlier URL tests: file links must not enter the automatic media loader,
and literal code must not load photos. They reuse the existing XCTest target and
require no server. Simulator captures are a separate read-only smoke
pass, and do not establish physical-device haptics or frame-rate performance.

## Native conversation

Chief, Orchestrator and Ticket share a latest-message line and a composer. Tapping
the line pushes the full conversation onto the current destination's page path
(`AppRoute.conversation`), with the native back button and a ⋯ menu in the bar; Focus
and Full are in ⋯. The docked composer exists only while its page is the top page, so
an open conversation never has a second composer under it. Back or an edge swipe
returns to the same page and scroll position; completed
navigation gives light haptic feedback. Both composers share the same draft and
conversation model. The chat uses the page surface with raised outgoing-message
bubbles, the thread and input; it does not repeat the latest-message line. The page's bottom bar remains
available for sending without opening the chat. At rest the line remains visible
while typing and recording,
with the latest message from either side and “You:” for outgoing messages.
Working uses the server's current running state.

The empty composer is at least 48 points tall with a plus and microphone. Text
opens a card with the input above the controls. At accessibility text sizes the
model choice stacks above the action row, and icon sizes stay within
their tap targets. Labels can grow with the text size. The bottom uses the
system safe area plus one point, matching the 35-point reference on the target
phone. Backend names and marks come from the server's driver list. The three built-in
backends keep their bundled marks; another backend shows its PNG icon, or its initials.
The model sheet reads the backend catalog and applies the selected model
and reasoning to the next message. A message names a model or reasoning only when
the pick differs from what the conversation runs on. The server holds a message
that names a model until a new turn, so an unchanged pick would turn every steer
into a wait. The selection follows the conversation until the owner picks another
value. An existing conversation keeps its backend.
The plus menu accepts files, library pictures, camera photos and a slash command. A message
uses the default delivery mode: steer, or queue when the conversation's backend cannot join
a running turn. Stop interrupts the running turn. The separate
microphone remains available while working or editing a draft.

A message appears at once with no delivery label, and the outbox sends it again
with the same ID until the server answers. The local message and its server row
share one visual identity and position. Only a refusal shows: a red mark that
offers Try Again in place. Suppressed delivery says why the message was not
delivered. A message the server holds, such as attachments or a model change
waiting for the turn to end, appears above the composer with the server's reason,
Steer in and removal, including after relaunch. Receipts settle delivery even when
the corresponding row falls outside the loaded window.
System and other-agent prompts retain their sender labels in the thread and latest
line; only owner prompts are attributed to You. Conversation pages share one refresh
error inside the composer; a read that cannot reach the server shows none. Speech errors, attachments and retry controls also stay
inside its border. Parent refresh failures never add a footer between the
input and keyboard.

A new reply follows the bottom only while the reader is already there; scrolling
up keeps the reader's position. Latest appears once the reader is more than 120
points from the bottom, and fades away within 32 points. Touching the thread or
making a small scroll does not make it flash. The 44-point button sits at the
lower right, 16 points above the conversation area boundary, clear of the line
and composer. A small dot means output arrived while the reader was away; it is
not an unread-message count. Tapping returns to the scroll view's bottom edge,
keeps the keyboard and draft, and resumes following through streamed completion.
The return uses the shared entrance motion; Reduce Motion makes it immediate.
See [the Latest review](ConversationLatest.md) for the diagnosis and focused proof.

The foreground surface subscribes to the existing Focus/Full tail and applies its
snapshot and change frames directly, without re-reading the transcript. A frame
reads only changed live text and receipts for unsent messages; the configuration
is read once per connection and again when the owner's latest message or the
turn's running state changes. A gap or reset reopens the tail for a fresh
snapshot. Full subscribes
to live output and reads its versioned text body. Streaming and recorded replies
share a visual identity. Focus follows the server's reply-only contract. Earlier
pages and deferred bodies load on demand. Opening finds the first unread owner
message, loading earlier pages if necessary, or returns to the latest line.
Speech starts on an explicit tap after permission. Words are held until recording
ends, then appear as an editable draft. Cancel discards recognition results. Nothing
sends automatically. Reduce Motion removes the composer shape animation. Navigation transitions follow
the system accessibility setting.

`ios/scripts/test-conversation.sh` runs the Foundation host checks.
`ios/scripts/test-conversation-simulator.py` provides an opt-in loopback stub and
UI scenarios in the same host for the keyboard, scroll position, delivery transitions, model
sheet, voice-draft flow, page opening/closing, queue controls after relaunch,
offline status geometry, largest-text controls and system sender attribution. The layout/attribution case can run independently of delivery waits.
It serves no driver list, so the app shows its fallback names and marks. With `--review`,
`--driver-review` adds `GET /api/drivers`, its icon route and two more backends: one with a
PNG icon and one with only an SVG icon, which shows initials. Hermes stays a backend the list
does not name, and its card lists no efforts, as on a real server. The same flag serves the
Ticket's backends by POST, makes the Ticket's worker setup editable, and serves Settings
Workers and the Chief's settings. Codex's
model resolves to a dated model the server names "Sol (November)".
`--hermes-conversation`, with `--driver-review`, runs the conversation on hermes with a stored
effort the card does not offer. `--no-steer` makes the conversation say its backend cannot
join a running turn, so a message queues.
The detail-page scenario reuses that host and the existing Ticket/Orchestrator
fixtures to check native push, edge swipe and Back, retained drafts and restored
page scroll positions. It also checks that the back button and ⋯ menu are in the
bar, no title shows, and Focus/Full works through ⋯. Ticket and Orchestrator run with both light and dark system appearances;
Panels retains its existing dark palette. The runner restores the previous system
appearance. It records screenshots of the pushed page, cancelled swipe and
restored parent. This native navigation boundary is absent from host model tests. It uses no additional server
or browser host.

It locks PanelsLaneA, uses the
separate task bundle, and does not boot, shut down or erase a simulator. Set
`PANELS_CONVERSATION_OUTPUT` to an external evidence directory. The optional
`PANELS_CONVERSATION_LANE`, `PANELS_CONVERSATION_DEVICE`, and
`PANELS_CONVERSATION_BUNDLE` select another assigned, already-booted lane, or a
task's own booted simulator named as the lane, and a separate task bundle. `PANELS_CONVERSATION_TEST=ConversationLatestTests` selects
the focused Latest scenario in the same host. Debug smoke mode
is restricted to loopback; without a valid stub URL it uses a closed loopback port.
The voice scenario injects recognition output: it does not prove microphone capture
or speech recognition. Physical-device haptics, VoiceOver, audio transcription,
camera capture and server read-marker writes remain outside this proof.

## App-wide feel pass

Chief and Orchestrator headers use the already loaded identity during
the initial detail read. Ticket navigation uses its cached title. Activity marks
reserve their width so arriving unread/working indicators do not rewrap titles.
Expanded recaps stay expanded during refresh. Ideas disclosures clip their
shrinking content to address the reviewed collapse overlap. System list headers
preserve mixed case; static group labels use title case.

The polish revision was compiled with the generic iOS Simulator SDK and four
build jobs, and the four existing host system-model tests passed. Lane B was shut
down and this task forbids booting it, so there was no simulator capture or runtime
navigation pass. The Ticket and Review redesigns are integrated and preserved.
Combined interaction review remains outstanding. Physical haptics, motion
and live regrouping still need runtime verification.
No live write was sent. No tests were added for these presentation changes.

## Accessibility polish

The Sidebar keeps its default font sizes but scales them with Dynamic Type. Worker,
settled and artifact rows wrap titles at accessibility sizes; worker group counts
stack below their labels. Artifact file types move below the name. Deployment
status can wrap, and Ticket has a full title in the scrollable content. Recaps
show their full text at accessibility sizes or with VoiceOver enabled, without
the measured clipping used by the compact preview. Settings, Usage and
VPS use menu pickers at accessibility sizes so segment titles do not truncate.

Conversation rows expose their title with unread and working state as one
VoiceOver stop. Worker groups announce count, unread count and expansion state.
Artifact Share/Copy link have named accessibility actions;
file and folder rows announce their type or item count. Main loading states name
what they are loading.

This pass was checked with a generic iOS Simulator SDK build and the four macOS
host model tests, without booting a simulator or contacting the live server.
Those checks do not verify rendered wrapping, VoiceOver focus/action behavior,
or physical haptics. A device accessibility pass remains necessary. After integrating the Review screen, approval stages expose their selected state,
approval and Send back retain labels while busy, and the revision field names its
purpose. Review actions stack at accessibility sizes and proposal/recap controls
expose their text to VoiceOver. No new tests were added for these
view-only edits: the existing host tests cover model contracts, while actual text
layout and assistive interaction require the deferred runtime check.

## Saved work and offline recovery

[Saved data and interrupted connections](Offline.md) describes the on-phone cache,
saved drafts, and the outbox that saves every message, decision, mark read and Review
recording, shows it as done at once and sends it while the app is in the foreground.

## Loading and retry states

Review, Chief, Orchestrator, Ticket and artifact lists share a labeled
delayed block skeleton on the first load. The retry screen appears only when nothing
has loaded and the server refused the read; a read that cannot reach the server
keeps the skeleton. Failed refreshes keep the last content visible, including an
empty result, with no note. Retry buttons remain disabled until their request
finishes. Artifact previews also retain their downloaded document after a failed reload.

Ticket supports pull-to-refresh. Chief and Orchestrator use the system pull
feedback without an extra haptic. Review's summary keeps its running-agent line and its return button.
Artifact image, PDF, media and Quick Look viewers keep the existing Reload
menu; text, markdown and HTML retain their existing refresh gestures.

These states are verified by a generic iOS Simulator SDK build only. The existing
four macOS host model tests also pass, but do not exercise SwiftUI states. No new
view-mirroring tests or connected hosts were added. Offline interaction, layout
stability, animation and haptic feel still require a later runtime/device pass;
this lane did not run a simulator or write to a live server.

## Scoped iOS fixes (pios-fix2)

Rows and page titles share one activity mark: an unread dot takes precedence over
the fixed-size working spinner, matching the web. They never appear together. Reduce Motion substitutes a static
working mark. Detail-page activity sits beside the full title in the scrolling page.
The Chief has no eyebrow or reserved eyebrow height; its header uses its natural
content height, as do orchestrator headers.

## Main release synchronization (2026-10-23)

The app includes main release `7379896f`, merged from the iOS branch's common
ancestor `ff1a4e5b`. Review and ticket approval keep their existing request shapes;
reviewer pickers offer only the owner, current parent orchestrator and Chief,
matching the server's stricter authority checks. Send-back, artifacts, native push
registration and the payload-free change stream retain their existing contracts.
The server now derives notification attention from current external requests and
suppresses obsolete internal review notices; iOS continues to read those facts.

Deployed appears only while both `valid_until` and `success_display_until` are
usable and in the future, with a connected stream.

Dependency-blocked workers take precedence over Agent and Errored on complete
worker pages. Loaded settled rows
sort by newest update, with identifier ties. Unread remains
a row mark, without a Messages group. Desktop rail geometry and browser bundle
reload prompts have no native counterpart. The shared conversation still uses
native scrolling; Latest returns to the newest loaded message, and opening
the page does not permanently enlarge the underlying page's reserved bottom space.

`SyncMainTests` contains two host checks: independent success deadlines (old coverage
had one deadline), blocked Agent classification (old coverage checked rail exclusion
only). They reuse the existing host package and decoder, with no connected host.

The iOS app no longer displays interruption accounts or manual worker restart in
Settings. The conversation Stop control only interrupts the current turn; it does
not expose interruption accounts or restart workers. The
remaining deployment status decoding does not require interruption fields.

## Ticket disclosure and conversation input spacing

Ticket Steps starts collapsed, with the total count visible in the same 50-point
row as Artifacts. The list opens inline; individual step links keep their reading
pages. Both disclosures use the existing plain toggle without an extra animation.

Speech updates trim leading and trailing whitespace from the recognized text and
the existing draft before joining them. Internal line breaks remain. The multiline
input uses its intrinsic height, within the five-line limit. The conversation
card relies on the system bottom safe area rather than adding another 12-point gap;
the latest line sits above it on the owning page.

Verification for this change: generic iOS Simulator build with four jobs, six
conversation host tests and six system/synchronization host tests. The added speech
test covers the previously untested trailing-newline draft path using the existing
host model, without a connected host. Locked PanelsLaneA fixture captures compare
the bottom spacing and show collapsed Steps. They do not exercise microphone
recognition, keyboard transitions, or live-server behavior.

Detail pages use the native navigation bar. The system back button and left-edge
swipe work, including cancelling partway through; ordinary horizontal content drags
keep their own behavior. Yours marks stage
ownership, separately from a conversation waiting for an answer. Its current field
is shown for reading rather than as a required answer form. Worker
rows give Yours the same attention emphasis as desktop.

Review's embedded reply field keeps the same horizontal alignment before and after
typing. A refused reply can be edited, including its attachments, and those edits
survive relaunch. Sending it again creates a new message identity while keeping
the read boundary of the refused attempt. A saved reply leaves the card at once; the
outgoing card keeps its scroll position through the short fade. Retry gives light feedback; Mark read
gives success feedback. Message and proposal headings share the same
top padding.

## Loading rings

All app loading indicators use the same ring: a two-point stroke, a 30%-opacity
track, and a solid quarter-circle arc, turning once every 900 ms at constant
speed. Working marks and compact controls are 14 points; small controls are 16;
other loading states and pull-to-refresh are 20. The colours come from the theme:
accent by default, faint for conversation activity, and ink on filled actions.
Reduce Motion keeps the ring and arc still. Unread dots still take precedence
over working rings, and loading conditions have not changed. The usage meter
remains a determinate progress bar.

Pull-to-refresh keeps SwiftUI's native gesture and async action. A small UIKit
bridge hides the native spokes and places the shared ring in the refresh control.
The simulator tests in `RingSpinnerTests` cover the new risks: ring animation
stopping for Reduce Motion and detachment, and the native refresh bridge keeping
its action and completion behavior. Existing model tests cannot exercise UIKit
layers or SwiftUI's refresh-control hierarchy. These tests reuse the app's unit
test host, stub the refresh work, and require no server or additional browser host.

## Server compatibility

Every native request sends `X-Panels-iOS-Build` from `CFBundleVersion`.
`CURRENT_PROJECT_VERSION` supplies this value in both the checked-in Xcode project
and `project.yml`. Increase it for each TestFlight upload. Build 1 is the first
build with this contract. Earlier headerless builds cannot show the update gate.

The server returns HTTP 426 with `error.code=ios_update_required` when a build is
unsupported. The app then shows “Update Panels from TestFlight” across all screens.
It stops new requests and outbox replay, and keeps drafts and pending identities.
The rejection remains on this device for that server and build, including relaunch.
A different server or a new installed build has a separate compatibility state.
Connection failures do not set this state.

The app reads each ticket’s saved workflow for its fields, stages, and approval
choices. Review cards come from the view's review window. The phone splits each
entry into one card per purpose, its proposal, each unread run and its ask, each
with its own identity and timestamp.
Goal-owned messages use the existing shared conversation view.

The `Native iOS` workflow runs the existing Swift host suites and an unsigned iOS
Simulator build on macOS. It runs when a push to `main` changes `ios/` files, and on manual runs, because GitHub-hosted Mac machines are scarce. A signed device check remains necessary for TestFlight,
Tailscale identity, microphone permissions, and actual APNs delivery.

## Mac execution release compatibility

Ticket detail and Review identify the Ticket's machine. Navigation preserves that identity through generated Ticket wire fields and the native presentation adapter. An unknown or missing machine identity cannot authorize a working mark. Worker rows on Manager pages
use the machine catalogue for identity and scoped health for current activity. A disconnected or
reconciling Mac retains unread messages but shows activity as unknown. The full
conversation and Settings show the last measured contact, rather than treating a
saved running state as proof the Mac is still working. Ticket setup and conversation
model pickers read the Ticket-scoped catalogue, including the server's last offline
copy; Chief and Manager model pickers continue to read the VPS catalogue.

Sends, approval, send-back, queue changes, stopping and read acknowledgments keep
the same Ticket and conversation identities on either machine. Permission and input
requests still direct the reader to the web to answer. The transcript distinguishes
an answer saved on VPS, applied on Mac, and application left unconfirmed. Creating
Tickets or selecting/moving their machine has no native flow yet. Machine installation,
updates and executor administration remain on the web. Existing scheduled-task detail
shows the schedule's machine when one is saved.

Usage reads the current date-range summary and refreshes pricing through the rates
endpoint before rescanning. Source failures, unpriced records and cached/unavailable
pricing stay visible. These figures describe the server's recorded CLI sources;
they do not establish a combined Mac/VPS usage total.
Broken Ticket records stay diagnostic rather than preventing the whole list from
loading. A diagnostic detail never offers lifecycle writes.
A Ticket's existing Goal reviewer is retained when editing approval limits.

Push registration remains disabled. Inline attachment parity
remains separate work. The compatibility checks use Foundation host tests, in-memory
HTTP stubs, backend checks and one unsigned generic iOS compile. They do not prove
physical-device behavior or a live Mac/VPS round trip from the iPhone.

The offline and conversation fixture scripts accept `--server-only` for a caller-owned
verification turn. This mode starts only the loopback fixture and writes
`fixture-ready.json` under its output directory. The caller supplies the test runner
environment, assigned device, build products and deadline. Fixture videos default off.
Optional native proof endpoints require the owned device and an active deadline.
Send TERM to the recorded fixture process to close its listeners. This mode permits
one build for multiple fixture cases without another simulator or build host.

## Goals

Each Sidebar card shows the status tag of its worst active goal. Chief and Managers
show their own goals with working checklists and manual charts. A binary goal without
a checklist offers Mark done or Mark not done. Manual charts offer their configured
increments, such as Add 100 words. A Manager with one goal displays that goal
under the Manager's name; multiple goals use cards. Holder documents use the same
native page renderer, including Tickets blocks; when none is authored the page
keeps its own paged Tickets list. All goals includes inactive goals and column filters.
Goal screens share a full project catalogue read for labels, filters and editing.

A goal page opens its holder's conversation. Previous goal transcripts use the
existing historical reader and remain read only. Retired goal file links cannot
open.
Ordinary Ticket and Manager files still use the native reader.
Project, deadline and state can be edited in place. Markdown keeps its links.
Tables keep their headers and rows in one horizontal scroll with aligned columns.
The surrounding page stays at the phone width. The caption stays outside the
scroll, and the left-edge back gesture retains priority. Tables sort without
changing the stored row order. Grids stack on the phone.

Charts have two sizes. Main charts use the web's small presentation. Supporting
charts and extra checklists fold in place; each chart remembers its range and
open state while the app runs. A line follows a finger and clears inspection on
release. Bars and rings retain the selected reading. Missing readings stay
missing, and comparisons use the server's supplied summary where one exists.
The server calendar controls deadline colours. Chart, number and checklist motion
uses the web's own springs and curves. When the range changes, a line reshapes
from what is on screen while its scale moves on the same spring. Numbers turn
digit by digit in the direction they changed. Reduce Motion makes every change
instant, as on the web.

Goal edits do not enter the outbox. Failed edits offer Retry save and Discard
edit. A stale revision cannot replace a newer
page. Manual retries preserve the original request identity, date and value;
the app never retries a measurement write by itself. There is no Saved toast.

Run `ios/scripts/test-offline.sh` for the Foundation checks. That host evaluates
the current web chart catalogue and curve functions as an independent comparison
for the native presentation code; it requires this worktree's web dependencies.
The Goals request test also checks that the project read supplies `detail=full`.
The existing simulator fixture host supports `PANELS_GOALS_SMOKE=1` and
`PANELS_OFFLINE_TEST=GoalFlowTests`. Supply an already booted assigned simulator,
a separate task bundle ID, lane lock name and output directory through its
`PANELS_OFFLINE_DEVICE`, `PANELS_OFFLINE_BUNDLE`, `PANELS_OFFLINE_LANE` and
`PANELS_OFFLINE_OUTPUT` settings. It uses loopback data and no live writes.

The one-off table and artifact review uses the same loopback fixture and
`PanelsReview` scheme. Set `PANELS_TABLE_SCROLL_SMOKE=1` and
`PANELS_OFFLINE_TEST=GoalFlowTests/testSharedTableScrollAndReviewArtifactTabs`,
then run `ios/scripts/test-offline-simulator.py --review` with the assigned device,
separate bundle, lane lock, and worktree output directory. It checks shared table
motion, a fixed page, edge Back at two table offsets, and Back from an artifact
Review opens directly. It never sends a message and stays outside the permanent
screen suite. This native touch and navigation boundary requires the real app
host; Foundation tests cannot prove gesture priority or Back.

## Decision notes

Ticket and Review keep Approve and Send back on the protected outbox path. Both
carry the complete note with the exact proposal revision and stable operation
identity. The shared server action saves the decision and attributed instruction
together. Full history shows the actual Worker prompt as a system message.

When a current guidance draft exists, the approval sheet displays its exact text
as a read-only note. The confirmation submits that displayed snapshot. It never
adds an undisplayed draft or reads a replacement note after the tap. This adds no
note editor or separate chat send.

Send back preserves whitespace, line breaks, and Unicode. A whitespace-only note
still cannot be sent. The outbox clears a note draft only after an applied receipt,
and only when its text and saved timestamp still match the captured draft. Waiting
and refused decisions retain drafts. Newer edits survive an older
receipt, including edits that restore the same text.

## Text entry and navigation

Each visible screen owns focus for its conversation fields and decision notes.
Review gives its current card's note, reply or answer field one owner.
In chat and Review, inert background taps clear that screen's focus.
The normal-priority tap gesture lets child text selection, controls, links, and inputs keep their interactions.
Those scroll areas also support interactive keyboard dismissal.

Chat and artifact navigation clear the outgoing screen's focus before the route changes.
Drafts, attachments, and expanded composers, including the Goal composer, retain their existing content and shape.
