# Panels for iOS

Native SwiftUI foundation, iOS 18 minimum. The app defaults to
`https://panels-vps.tail687c57.ts.net`; More → Settings changes and persists the
HTTPS server address. The Mac must be able to reach the server over Tailscale.
No backend changes or third-party packages are needed.

The app icon uses the Chief of Staff portrait from
`web/src/assets/chief-of-staff-profile.webp`, centered on its black background.
Its opaque 1024×1024 PNG lives in `Panels/Assets.xcassets/AppIcon.appiconset`;
`project.yml` selects `AppIcon` for the Panels target.

Workspace is the initial tab. Review, Workspace and More each own a navigation
stack and retain their place when switching tabs. More groups Work and System in
a native list, with deployment status in a Panels server row above them. Active
deployment status adds a dot to More; healthy idle has no dot. The tab bar is visible on these three roots; the shared `AppRoute`
destination hides it on detail pages and their pushed children. Roots use the system
default visibility: explicit root overrides compete with the shared detail rule.
Navigation uses native pushes and back swipes;
every destination has a separate source file.
The existing full ticket API supplies ticket detail. On 2026-10-22, the lighter
`/api/tickets/{id}/workspace` endpoint returned HTTP 500 for the sampled tickets;
`/api/tickets?detail=full&id=…` succeeded. This adapter deliberately uses that
working endpoint rather than inventing a new backend route. Its saved `workflow`
supplies Ticket and Review controls, stage names and approval gates. The nullable
`worker_type` identifies the creation preset and never supplies those controls.

## Refresh work

While the app is in the foreground it follows one navigation view stream for
what is on screen, as desktop does: a snapshot first, then patches. Review is
always in that scope, for its badge. Workspace adds its rail windows while it is
visible: active orchestrators, Chief's tickets and each card's tickets. A detail
page adds its own record (and, for Chief and orchestrators, a children window). A
new demand opens a stream for the new scope while the last view stays on screen,
unless the running stream already carries it, as for a Ticket opened from
Workspace or Review; leaving a screen only narrows the next stream. One coherent view replaces the
earlier Review, manager and ticket list reads; the phone no longer reads
`/api/review`. A launch shows the last saved view at once. A return to the
foreground, a dropped connection, a gap, a reset or a closed stream all start a
fresh stream, whose first frame is a fresh snapshot. A stream that was current
reconnects after one second, as the web does; failures retry after 1, 2, 4 and
then 8 seconds and show Reconnecting. Then saved work drains, as before.

Chief settings and deployment status are outside the view, and deployment status
is not a navigation change at all, so those two keep the payload-free change
stream and the resource-version API: a wake probes both versions, at most once a
second, and unchanged versions need no body read. A body is accepted with the
version from the same server snapshot. A failed or unknown version check keeps a
full read, and servers without that API keep ordinary reads. Manual refresh reads
both again and reconnects a view stream that is not current.

A detail page reads its own detail when it opens, after a return to the
foreground or a recovered connection, and otherwise only when its own records in
the view change or leave it: a Ticket and its step pages on the Ticket record;
an orchestrator on its record and its children window; Chief on its record, its
active children and its settings. Working and unread marks on those pages come
from the view, so a mark change needs no detail read. A Review proposal reloads
when its Ticket's record changes. Settled reads again when the settled count
changes. An inline artifact list reads on expansion and when its owner's file
count changes. Settings, Usage, Feedback and Ideas read when opened, on return
to the foreground, after a recovered connection and on pull-to-refresh.

`ResourceRefreshTests` adds one host test for the stream, which no earlier test
reached: a patch applies without a read, a gap opens a fresh stream, a new detail
scope opens its own stream, a withdrawal opens none, and a record's reload stamp
moves only with that record. It reuses the existing URLProtocol fixture in the
offline host package. The offline simulator stub serves the events stream as a
snapshot frame each second.

The view code lives in `Core/Navigation`. The wire subset is hand-written Codable,
pinned to projection 7 and wire 3, and ignores fields the server adds. Ticket,
orchestrator and Review facts are derived from view records the way desktop's
`presentation.ts` derives them, then mapped into the existing native models.
Frame application (snapshot, checkpoint, patch, reset; a gap needs a fresh
snapshot) is a pure unit that each stream applies to its own snapshot.
The server bounds a view to 256 rows and 60 per window. Mounted screens share
that budget the way the web owner shares it. Workspace asks for every card's tickets in one
scope, so with many cards each loads fewer rows; a card cut short ends with
“+N more”, which opens its page. Orchestrator and Chief pages keep
their own detail reads, which also supply goals, recaps and artifacts. Settled
keeps its paged list read, and reads only while open; its count comes from the
view.

The full conversation acknowledges remote rows only after they enter its visible
scroll area. Polls, stream progress, the dock, hidden screens and inactive scenes
earn no read credit. A deferred message earns credit only after its body is loaded
and visible. The request carries that row's exact conversation and sequence;
confirmed read positions never move backwards when an older configuration arrives.
A failed acknowledgment leaves the messages unread.
Overlapping refresh requests share a following read instead of being dropped.
The transcript projection is retained until its message, expanded body, outgoing
receipt, live text or ordering changes. An unchanged live-output reference reuses
its text; a changed byte count, version or body identity reads it again. Image
artifacts load their image once per successful document load, outside the view
body. A failed reload retains the previous document and image.

## Typography

Shared text roles in `Core/Theme/Theme.swift` follow the mobile web's weights.
Review titles and completion text use medium (500); ticket and manager titles
use semibold (600). Proposal decisions use medium, while message actions and
message prose remain regular. Feedback text and notification labels use medium. Ideas capture and configuration headings use medium;
artifact filenames remain regular. Section headings follow their web surface:
regular for navigation and VPS, medium for configuration and Usage.

`panelsNavigationTitle` sets the weight on each native navigation item. Usage
keeps its semibold heading; other utility pages use medium. This retains native
navigation geometry and restores the destination's style on back navigation.
Font sizes and Dynamic Type remain native. Markdown headings retain their
existing rendered body size, with the web's bold h1/h4–h6 and semibold h2/h3;
the inline renderer accepts the shared font so its body default cannot mask it.

## Build and run headless

Use Xcode 16 or newer with an installed iOS 18+ simulator runtime. The committed
Xcode project builds without XcodeGen. If adding source files, install XcodeGen
(`brew install xcodegen`) and run `ios/scripts/generate-project.sh`.
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
xcrun simctl launch "$PANELS_DEVICE" com.khushaljagota.panels
xcrun simctl io "$PANELS_DEVICE" screenshot "$PANELS_PROOF/workspace.png"
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
| PanelsLaneB | `566D051B-B6BB-4046-9038-66C05CCC2B96` |
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

`PanelsNotificationUITests` holds the notification and opt-in conversation UI
scenarios. `PanelsTests/Fixtures` contains response samples from read-only GETs on
2026-10-22; the full Ticket sample includes the current saved workflow controls.
Tests protect two previously uncovered
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
reads. The shell owns `store.run()`, which follows the view stream and the change
stream, cancels both in background and reconnects after disconnect. Screens state
their windows with `store.setNavigationDemand`; detail pages use `.navigationDemand`
and key reloads with `.reload(onChangeOf:)` on `store.ticketStamp`,
`managerStamp` or `chiefStamp`. Overview reads run
concurrently and retain previous data with an explicit error on failure. Workspace
shows Chief tickets and up to 60 active orchestrators; Previous and Next replace
that page when there are more, as the web's window pages do. A manager's detail
supplies its complete grouped tickets. Tickets whose parent is settled stay inside
Settled, so a settled parent never gets a second active card.
Artifact directories refresh on selection and pull-to-refresh because filesystem
writes need not emit a database change.

`ConversationNavigation` supplies the shared composer and conversation page on Chief,
Orchestrator and Ticket, described below. Artifact viewing uses native file screens. Usage, VPS and Settings have native read-only
screens. Feedback implements the Open/Handled inbox, grouped Ticket links, page
context links and Dismiss/Reopen actions through its API adapter. It reads when opened,
on return to the foreground, after a recovered connection and on pull-to-refresh. Its empty state omits
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
pages after live updates. Detail-page Back and page actions sit in the content margin and scroll away
with the page. No navigation-bar title appears at any scroll position.

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
limits, proposal acceptance/return, field/title/recap editing,
blocker removal and pristine worker setup. Live simulator verification is read-only; ticket and Feedback writes are
exercised only with an injected stub or URLProtocol interception.

Review supports approval and send-back through its API adapter; see
`Review.md` for its behavior and focused verification. Live-server simulator
verification remains read-only; mutation verification uses a mocked transport.

Haptic helpers offer light, success, error and selection. By the owner’s
2026-10-22 override, tabs give selection feedback and shell push/back changes
give a light impact, including Workspace navigation. Disclosures and read retries
are silent; pull refresh and context menus retain system feedback. Selection also
acknowledges a changed control value.
Ideas and Feedback use light feedback when submitting and success or error on
the result; page context actions and edits acknowledge the result only. Copy and
local Settings saves acknowledge completion. Background updates never buzz.
Native pushes retain system motion; shared disclosures use the same bounce-free
spring, with instant changes under Reduce Motion.

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
Pull-to-refresh and the toolbar refresh button request fresh provider allowances
and reread local CLI records, just like Usage on the web. Existing readings stay
visible during refresh, with each source’s last-read time; failed provider reads
keep the previous allowance and show the error. Refresh gives haptic feedback.
Opening the screen only reads stored values. Cost estimates are not bills.
The Panels server row in More displays deployment status without opening a page;
VPS has its own navigation row.

VPS reads machine measurements, CPU history, services and paged processes. Its
15-second foreground poll preserves the loaded process window. CPU history offers
5 minutes, 24 hours and 30 days; points do not connect across missing samples.
The app never renews the server's activity lease. Stale readings keep their sample
time and show “No feed”. Pull-to-refresh also reloads history.

Settings reads agent launch defaults, role skills, schedules and notification
preferences. Server preferences are read-only; management remains on the web.
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
are independent. Chief activity comes from the navigation view's Chief record.

The approved mobile design keeps hairline cards, full-width 32pt group bars with
sentence-case labels on the cards’ 16pt inner line, one-line
rows at standard text sizes and native pushes. Accessibility text sizes wrap row titles. Settled is a plain expandable list after the cards, with
the server's total count. Opening scrolls its header to the top, then fades in
the rows. Closing fades out the rows and returns to the previous scroll offset.
Pages load automatically when the last ten rows become visible; refresh preserves
the loaded window. Surviving rows retain order, and section changes fade instead of travelling.
Regrouping waits during scrolling and context previews; titles and marks stay live.
Reduce Motion removes layout animation.

Long press previews the full title, recap and saved-workflow stage sentence.
Its menu opens the page, requests a recap, backlogs/resumes a ticket, or settles an
orchestrator. These use existing endpoints. Failed or uncertain actions show an
error; confirmed actions give success haptics. Pushes use the owner-approved light feedback; the Settled toggle gives
selection feedback, and pull refresh/context menus use system feedback. Every shared route
destination hides the tab bar; returning to a tab root restores it. Physical haptic feel
still needs a device. Conversation contents use the shared native conversation page described below.

Four focused Workspace tests reuse the existing ticket fixture and an in-process
URLProtocol stub. They cover attention precedence, hidden-ticket exclusion, stable
section ordering, and write methods/payloads including uncertain recap delivery.
They add no backend or browser host. Live Simulator verification invokes no writes.

## Artifact integration

Ticket and Orchestrator pages expand their artifact catalog beneath the Artifacts
row. `ArtifactsRow(owner:count:)` supplies the shared 50pt entry row and count;
it is absent at zero. Chief has no artifact catalog. Folders expand in place too.
`AppRoute.artifact(owner:path:title:)` opens a specific managed file. Catalog links
retain their actual file owner, including links to files on another ticket.

The inline list reads on expansion and when the owner's file count changes, with paging and a search
field for larger catalogs. HTML runs
inside a sandboxed frame in a nonpersistent WKWebView, with relative file assets
and native navigation for managed links. HTML previews offer Phone width and Fit
desktop. Fit desktop scales a 1024-point canvas into the available width; pinch
zoom lets the reader inspect a fixed-width design without changing its styles. Folder sections show five files before
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
viewer uses the same native conversation page as Ticket and Orchestrator,
including image previews. Files opened from an inline catalog keep that catalog's
conversation owner while managed links retain their actual file owner. Further
artifact links preserve the conversation context. The viewer resolves the current
conversation ID from the owner and uses the existing owner send door. Context-menu
previews set `showsConversation: false`. Listing, opening, reloading and sharing
artifacts do not write to the server; sending a message requires an explicit tap.


The three artifact logic tests cover cross-owner URLs and encoding, unsafe file
paths, and preservation of markdown code blocks. They reuse the existing XCTest
target and require no server. Simulator captures are a separate read-only smoke
pass, and do not establish physical-device haptics or frame-rate performance.

## Native conversation

Chief, Orchestrator and Ticket share a latest-message line and a composer. Tapping
the line pushes a conversation page from the right using the native navigation
stack. The same subtle Back and ⋯ controls as the Ticket page replace the system
navigation bar and conversation title. Focus and Full are available in ⋯. Back
or an edge swipe returns to the same page and scroll position; completed
navigation gives light haptic feedback. Both composers share the same draft and
conversation model. The chat uses the page surface with raised outgoing-message
bubbles, the thread and input; it does not repeat the latest-message line. The page's bottom bar remains
available for sending without opening the chat. At rest the line remains visible
while typing and recording,
with the latest message from either side and “You:” for outgoing messages.
Working uses the server's current running state.

The empty composer is at least 48 points tall with a plus and microphone. Text
opens a card with the input above the controls. At accessibility text sizes the
model and delivery choices stack above the action row, and icon sizes stay within
their tap targets. Labels can grow with the text size. The bottom uses the
system safe area plus one point, matching the 35-point reference on the target
phone. The model sheet reads the backend catalog and applies the selected model
and reasoning to the next message. A message names a model or reasoning only when
the pick differs from what the conversation runs on. The server holds a message
that names a model until a new turn, so an unchanged pick would turn every steer
into a wait. The selection follows the conversation until the owner picks another
value. An existing conversation keeps its backend.
The plus menu accepts files, library pictures, camera photos and a slash command. Steer, Queue and Send now
use the existing delivery modes. Stop interrupts the running turn. The separate
microphone remains available while working or editing a draft.

Messages appear immediately with Sending, Queued or an explicit failure.
Successfully delivered messages have no delivery label in the thread.
The local message and its server row share one visual identity and position.
Confirmed refusal and connection failures offer Retry in place. A timeout or lost
response remains uncertain and cannot be retried blindly. Suppressed delivery
says why the message was not delivered. A held message shows the server's reason,
such as attachments or a model change waiting for the turn to end. Held messages are recovered from the
conversation detail. Its held-prompt records make Steer in and removal available,
including after relaunch, independently of delivery-note wording; receipts settle delivery
even when the corresponding row falls outside the loaded window.
System and other-agent prompts retain their sender labels in the thread and latest
line; only owner prompts are attributed to You. Conversation pages share one refresh
error inside the composer. Speech errors, attachments and retry controls also stay
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
The detail-page scenario reuses that host and the existing Ticket/Orchestrator
fixtures to check native push, edge swipe and Back, retained drafts and restored
page scroll positions. It also checks that the shared Back and ⋯ controls match
the parent page, the title and system bar are absent, and Focus/Full works through
⋯. Ticket and Orchestrator run with both light and dark system appearances;
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

Chief and Orchestrator headers use the already loaded Workspace identity during
the initial detail read. Ticket navigation uses its cached title. Activity marks
reserve their width so arriving unread/working indicators do not rewrap titles.
Expanded recaps stay expanded during refresh. Ideas disclosures clip their
shrinking content to address the reviewed collapse overlap. System list headers
preserve mixed case; static group labels use title case.

The polish revision was compiled with the generic iOS Simulator SDK and four
build jobs, and the four existing host system-model tests passed. Lane B was shut
down and this task forbids booting it, so there was no simulator capture or runtime
navigation pass. The Ticket and Review redesigns are integrated and preserved.
Combined interaction review remains outstanding. Physical haptics, motion,
all-detail tab restoration and live regrouping still need runtime verification.
No live write was sent. No tests were added for these presentation changes.

## Accessibility polish

Workspace keeps its default font sizes but scales them with Dynamic Type. Worker,
settled and artifact rows wrap titles at accessibility sizes; worker group counts
stack below their labels. Artifact file types move below the name. Deployment
status can wrap, and Ticket has a full title in the scrollable content. Recaps
show their full text at accessibility sizes or with VoiceOver enabled, without
the measured clipping used by the compact preview. Settings, Feedback, Usage and
VPS use menu pickers at accessibility sizes so segment titles do not truncate.

Conversation rows expose their title with unread and working state as one
VoiceOver stop. Worker groups announce count, unread count and expansion state.
Workspace actions and artifact Share/Copy link have named accessibility actions;
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
persisted drafts, message and decision outbox, Review audio recovery and the exact
server guarantees. The foreground app drains saved work after reconnect or relaunch.

## Loading and retry states

Review, Workspace, Chief, Orchestrator, Ticket and artifact lists share a labeled
delayed block skeleton on the first load and a native retry screen when nothing has loaded. Failed
refreshes keep the last content visible, including an empty result, with a small
retry notice below the content. The notice does not insert a row above existing
content. Retry buttons remain disabled until their request finishes. Artifact
previews also retain their downloaded document after a failed reload.

Ticket supports pull-to-refresh. Chief and Orchestrator use the system pull
feedback without an extra haptic. Workspace pull-to-refresh waits for its activity
and Settled reads too; a failed activity read retains the last signal with an error
notice. Review retains its quiet empty state, running-agent count and revisit action.
Ticket retains its add-orientation action for an empty recap. Artifact image, PDF, media and Quick Look viewers keep the existing Reload
menu; text, markdown and HTML retain their existing refresh gestures.

These states are verified by a generic iOS Simulator SDK build only. The existing
four macOS host model tests also pass, but do not exercise SwiftUI states. No new
view-mirroring tests or connected hosts were added. Offline interaction, layout
stability, animation and haptic feel still require a later runtime/device pass;
this lane did not run a simulator or write to a live server.

## Scoped iOS fixes (pios-fix2)

Rows and page titles share one activity mark: an unread dot takes precedence over
the fixed-size working spinner, matching the web. They never appear together. Reduce Motion substitutes a static
working mark. Detail-page activity sits beside the full title in the scrolling page. Workspace cards use the same 16-point top inset and title-to-first-group gap.
The Chief has no eyebrow or reserved eyebrow height; its header uses its natural
content height, as do orchestrator headers.

Verification: the four-job iOS Simulator build and four existing conversation host
tests passed. A stub-only, locked PanelsLaneB pass captured Workspace and the
Orchestrator title with simultaneous working/unread marks; its accessibility
snapshot also confirms the inline artifact list expanded. The temporary UI driver
stopped on row selector mismatches before opening the file or sending a message.
Firstmate accepted the captures and requested compile-only verification after
rebasing. No live-server writes, simulator lifecycle changes, new permanent tests,
or physical-device validation were performed.

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
worker pages; Workspace continues to exclude blocked work. Loaded settled rows
sort by newest update, with identifier ties. Unread remains
a row mark, without a Messages group. Desktop rail geometry and browser bundle
reload prompts have no native counterpart. The shared conversation still uses
native scrolling; Latest returns to the newest loaded message, and opening
the page does not permanently enlarge the underlying page's reserved bottom space.

`SyncMainTests` contains two host checks: independent success deadlines (old coverage
had one deadline), blocked Agent classification (old coverage checked rail exclusion
only). They reuse the existing host package and decoder, with no connected host.
The conversation host test adds the new suppressed fate so it cannot be mistaken for uncertain delivery or retried.

The iOS app no longer displays interruption accounts or manual worker restart in
More. The conversation Stop control only interrupts the current turn; it does
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

Detail pages hide the native navigation bar. Quiet Back and page actions live
in the page's own scrolling top margin, aligned with its content edge. The system
left-edge back gesture remains available, including cancelling partway through;
ordinary horizontal content drags keep their own behavior. Yours marks stage
ownership, separately from a conversation waiting for an answer. Its current field
is shown for reading rather than as a required answer form. Workspace and worker
rows give Yours the same attention emphasis as desktop.

Review's embedded reply field keeps the same horizontal alignment before and after
typing. A refused reply can be edited, including its attachments, and those edits
survive relaunch. Sending it again creates a new message identity while keeping
the read boundary of the refused attempt. In-flight and uncertain replies stay
locked. During a successful retry the outgoing card retains its status, Retry row
and scroll position through the short fade. Retry gives light feedback; a confirmed
Mark read gives success feedback. Message and proposal headings share the same
top padding. Chief's Workspace link extends its touch area into the existing card
padding, preserving its visible spacing.

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
choices. Review cards come from the view's review window, grouped by ticket as on
desktop. Each card's proposal and messages stay separate native cards, each with
its own identity and timestamp.
Goal-owned messages use the existing shared conversation view.

The `Native iOS` workflow runs the existing Swift host suites and an unsigned iOS
Simulator build on macOS. A signed device check remains necessary for TestFlight,
Tailscale identity, microphone permissions, and actual APNs delivery.

## Mac execution release compatibility

Ticket detail and Review identify the Ticket's machine. Navigation preserves that identity through generated Ticket wire fields and the native presentation adapter. An unknown or missing machine identity cannot authorize a working mark. Workspace rows and their
long-press previews omit machine labels. The server's
executor health decides whether a Mac working mark is current. A disconnected or
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

Push registration remains disabled. Dock previews and inline attachment parity
remain separate work. The compatibility checks use Foundation host tests, in-memory
HTTP stubs, backend checks and one unsigned generic iOS compile. They do not prove
physical-device behavior or a live Mac/VPS round trip from the iPhone.

## Goals

Active goals sit inside their Chief or Manager holder's card in Workspace. A tap
opens the goal; a long press shows its main display and an Open action. A binary goal without a checklist
also offers Mark done or Mark not done. Manual charts offer their configured
increments, such as Add 100 words. Chief and Managers show their own goals with
working checklists and manual charts. A Manager with one goal displays that goal
under the Manager's name; multiple goals use cards. Holder documents use the same
native page renderer, including Tickets blocks; when none is authored the page
keeps its own paged Tickets list. All goals includes inactive goals and column filters.
Goal screens share a full project catalogue read for labels, filters and editing.

A goal page opens its holder's conversation. Previous goal transcripts use the
existing historical reader and remain read only. Retired goal file links cannot
open; old queued goal sends keep their words without replay.
Ordinary Ticket and Manager files still use the native reader.
Project, deadline, state, title and explanation can be edited in place. Saved page
text can be edited too; Markdown keeps its links and offers Edit on a long press.
Tables sort without changing the stored row order. Grids stack on the phone.

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

Tapping a manual reading turns the number into a field with the whole reading
selected. Cancel and Done sit above the keyboard, no copy menu covers the
number, and a whole reading shows without a decimal.

Goal edits do not enter the offline message outbox. Failed edits keep their draft
and offer Retry save and Discard edit. A stale revision cannot replace a newer
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
