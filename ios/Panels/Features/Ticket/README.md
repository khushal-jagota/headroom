# Ticket page

The native ticket page reads the existing full ticket endpoint and that ticket's
worker-type manifest. The manifest supplies stage names, step order, completion
counts and the approval-limit options. A worker that still owns a step keeps its
“Currently working on” heading even between turns; only a live conversation signal
shows the spinner. Working and unread marks sit beside the project and worker type,
above the title. The recap has five visible lines before Show more, following the
owner's design update.

The recap and step bodies expand in place. The approval limit opens a native sheet;
Cancel discards its selections and Save writes the selected limit and reviewer.
Changing only the reviewer does not send a ceiling. Backlog and Resume include the
record hash that the server checks. Asking for a recap reports refused or uncertain
delivery without claiming success. These actions retain the existing API behavior.
The page reloads after a successful command and follows the shell's live revision.

The artifact band reads its own catalog on arrival, expansion and pull-to-refresh,
since file writes need not produce a database change. It opens the shared typed
artifact routes. The conversation remains the foundation's placeholder. Proposal
text can be read here; proposal approval and return controls belong to Review.

Local expansion uses the shared short motion duration and respects Reduce Motion.
Navigation and the approval sheet use native transitions. Controls use the shared
haptic helper; physical-device haptics are not verified by Simulator captures.

## Focused checks

`PanelsTests/TicketTests.swift` reuses the foundation's full-ticket fixture and adds
four small tests with no server or browser host:

- Manifest ordering and renamed labels: the existing decoding checks do not prove
  completion counts or approval-option floors. One pure presentation check covers
  this distinct risk with a small worker manifest.
- Mutation contracts: a URLProtocol stub checks backlog/resume hashes, recap target,
  and reviewer-only versus ceiling changes. Foundation tests were read-only, so
  they could not catch an incorrect write endpoint or overbroad payload.
- Failure outcomes: a stubbed stale-record rejection must remain an error, and a
  refused or uncertain recap must not appear successful. This uses the same stub
  and fixture and makes no live writes.
- Cross-ticket artifacts: the live catalog includes links owned by another ticket.
  One pure route check prevents opening that path under the wrong ticket; the
  existing artifact decoding check does not cover navigation ownership.

The external capture harness is task proof, not an additional maintained test
suite. It opens real ticket data on PanelsLaneB, expands sections, and cancels the
approval sheet without saving. Artifact viewing is a separate worker's route.
