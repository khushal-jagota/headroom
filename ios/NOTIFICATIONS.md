# Native notifications

## What Panels does today

The backend has durable attention edges and a Web Push delivery queue. There is
no notification history/feed API and no APNs sender. `/api/changes` is a
content-free foreground refresh signal, not a notification stream. Polling it
cannot wake a suspended iOS app. The native app already follows it while open.

`src/planner/notifications/contracts.py` defines five event types. Tickets offer
all five; Chief and managers offer message, answer and error notifications.
`attention.py` records false-to-true transitions. `logic/policy.py` applies the
saved preferences and constructs a title, body, route and replacement tag.
`runtime.py` and `web_push.py` deliver those intents to browser subscriptions.
`web/src/routes/NotificationsRoute.svelte` manages preferences and Web Push
subscriptions. An APNs token is not a Web Push subscription: it has neither a
browser endpoint nor the browser encryption keys.

| Event | Lock-screen example | Needed from Khushal |
| --- | --- | --- |
| awaiting_reply | Chief of Staff · Message / There is an unread message. Open Chief to read and reply. | Read and reply |
| awaiting_answer | iOS app · Needs an answer / The worker is waiting. Open the ticket and answer the question. | Answer |
| awaiting_approval | iOS app · Needs approval / The implementation is ready. Review and approve the proposal. | Decide on the proposal |
| assigned | iOS app · Assigned to you / The next stage is yours. Open the ticket to complete it. | Complete the stage |
| errored | iOS launch · Error / The orchestrator's turn failed. Open it to inspect the error. | Inspect and decide what to do |

These are payload examples, not generated claims about a live ticket. The sender
must use the real subject label and only assert details established by the edge.
Use a short subject label plus event in the title, one short action sentence in
the body, and keep the full context in Panels. Truncate long subject labels at a
word boundary around 60 characters. Never put prompts, secrets or full conversation
contents on the lock screen. Do not claim a reply is necessary for every unread
message unless the source actually establishes that.

## App implementation

Settings → Notifications offers the system permission request and recovery through
iOS Settings after denial. The app checks permission on foreground entry, asks
APNs for the current token, and handles registration success/failure. It does not
persist tokens as permanent identifiers. An enabled server integration upserts a
fresh token on launch, handles rotation and server changes, and exposes failure
and an explicit retry. A token is never printed in logs.

The notification delegate is installed at launch. Alerts appear as banners, in
Notification Center, and with sound while the app is foreground. Tapping opens a
known native route. A pending tap survives the shell's initial creation. Opening
an alert never accepts a proposal, marks a conversation read or sends a message.
A missing, unsafe or unknown route shows a calm error. The app refuses a payload
for a different configured server and never changes the server automatically.
Conversation reading and replies remain owned by the destination page.
Notification handling only opens that page.

The current build intentionally reports **Server delivery is not connected yet**.
`APNS_REGISTRATION_ENABLED=NO` prevents all device-registration requests. Merely
allowing notifications does not claim that background delivery works. The build
can still receive Simulator injections, which bypass the provider and APNs.

## Proposed native contract — server work remains

No backend file is changed by this delivery. The following endpoint is a proposed
handoff contract, not an endpoint already available in Panels:

```
PUT /api/notifications/apns/devices
Content-Type: application/json

{"device_token":"lowercase hex", "bundle_id":"app.panels.ios", "environment":"development"}
```

Return any 2xx only after the device is durably registered. The app uses the same
URLSession and configured server as its API client. The server must authenticate
the owner through the deployed access boundary, validate the allowed bundle IDs,
validate the token and environment, and idempotently upsert the owner/device
registration. Never treat possession of a device token as authentication. If the
native client needs additional credentials at deployment, provide them through
its API session before enabling this integration. Add owner-scoped revocation and
remove expired tokens on APNs `Unregistered`; do not log tokens. Changing servers
in this preview rejects old-server taps but does not revoke the old registration;
server-side revocation must be available before rolling out multi-server use.

Add an APNs delivery adapter alongside Web Push using the same attention edges,
preferences and policy. Browser and native deliveries need independent queue
records, delivery attempts and expiry cleanup; a browser success must not consume
the native delivery. Send only on a new attention edge, not every refresh. Apply
bounded retries to transient APNs errors and reject invalid tokens permanently.
Use the development or production APNs endpoint corresponding to registration;
set `apns-topic` to the exact registered bundle ID, `apns-push-type: alert`, and
`apns-priority: 10`. Set a bounded expiration, and use the subject replacement tag
as the basis for a <=64-byte collapse ID. Prevent one device's invalid token from
blocking others. Preserve useful event text and current preferences.

Payload (custom fields are siblings of `aps`):

```json
{
  "aps": {
    "alert": {
      "title": "iOS app · Needs approval",
      "body": "A proposal is waiting. Open Review to approve or return it."
    },
    "sound": "default",
    "thread-id": "panels-ticket-t_42"
  },
  "server_url": "https://panels.example.com",
  "route": "/#/review?ticket=t_42"
}
```

Keep the whole APNs payload under 4 KB. No badge is sent until the server owns a
correct authoritative count. `server_url` must be the canonical HTTPS origin the
app uses, without credentials, query or fragment. Supported routes:

- `/#/review` opens Review; `/#/review?ticket=t_42` opens that ticket in Workspace,
  matching Review’s ticket-opening behavior; its current proposal is shown there. There is one pending proposal per ticket.
- `/#/workspace/t_42` opens a ticket.
- `/#/workspace/item/m_9` opens an orchestrator.
- `/#/agents/chief-of-staff` opens Chief.

The last three are the existing backend policy routes. Approval senders can use
the Review route. Identifiers must be single alphanumeric/underscore/hyphen
components. Unsupported/deleted targets must remain read errors, not become
implicit actions or a different target.

## Apple account and release work remains

1. Register the production bundle ID in the intended Apple Developer team and
   enable Push Notifications. Register a distinct test App ID if physical-device
   tests use `app.panels.ios.pios-notify`.
2. Create or select an APNs authentication key with the correct team, key ID and
   topic permissions. Store the `.p8` private key only in server secrets. Build
   short-lived APNs provider JWTs on the server; never embed that key in the app.
3. Set `DEVELOPMENT_TEAM`, enable signing, and regenerate provisioning profiles
   including the push entitlement. This repository's default unsigned Simulator
   build cannot establish physical-device APNs registration.
4. `Panels/Panels.entitlements` uses `APNS_ENVIRONMENT`. Set `development` for a
   development profile and `production` for TestFlight/App Store; set the same
   value for the registration body. Inspect the signed archive entitlement.
5. Deploy the authenticated endpoint and sender, verify the app's access session,
   then build with `APNS_REGISTRATION_ENABLED=YES`. Leave it NO before that point.
6. On a signed physical device verify token upsert, every event type, opt-out,
   failed registration/retry, foreground/background/terminated taps, token
   rotation, invalid-token cleanup and notification settings changes. Test both
   APNs environments through actual Apple delivery. Simulator injection does not
   prove any of these server/Apple-account steps.

Apple references: [APNs registration](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns),
[notification responses](https://developer.apple.com/documentation/usernotifications/unusernotificationcenterdelegate),
[APNs provider requests](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns).

## Focused verification

`NotificationTests` fills three gaps in the existing model tests: untrusted
notification routes and cross-server taps; retaining a cold-launch tap until a
shell can consume it exactly once; and the native registration request/rejection
contract. It reuses XCTest and URLProtocol and needs no live backend. Existing
Feedback route tests do not exercise notification payload origins or callbacks.

`PanelsNotificationUITests` is opt-in. Its distinct risk is the boundary from
an OS-delivered notification through the delegate into SwiftUI navigation, with
the app foreground, background and terminated. Pure parser tests cannot prove
that system permission, delegate installation or actual notification taps work.
One test uses the already booted PanelsLaneB, four fixture payloads, and a temporary
loopback host that runs `simctl push`. The script holds
`lockf /tmp/panels-PanelsLaneB.lock` throughout; it never boots, shuts down or erases
a simulator. The test changes only its own app permission and performs read-only
navigation. No backend writes or APNs registrations are enabled.

```sh
PANELS_PUSH_TEST_OUTPUT=/tmp/panels-notification-proof \
  python3 ios/scripts/test-notifications-simulator.py
```

The loopback helper uses port 18763; survey it before starting. It stops when the
test exits. Results, screenshots and build output go outside the repository.


## Delivery evidence (22 October 2026)

The focused run passed on PanelsLaneB with bundle ID
`app.panels.ios.pios-notify`: three unit tests (0.033 seconds) and one UI test
(39.033 seconds) covering four injected alerts. It proved foreground banner and
tap, background Review tap, terminated-app orchestrator tap, and Chief tap. Eight
screenshots are attached to the result bundle. Ticket/orchestrator payloads use
synthetic IDs: the destination screens display their missing-record state. This
proves routing, not live proposal contents or successful APNs provider delivery.
The first permission request was accepted in the first run; subsequent runs
retained that permission without resetting the Simulator.

An initial run found an iOS 27 banner accessibility-type mismatch in the test.
The next run exposed a real background-resume crash: the async notification
response bridge invoked UIKit completion off-main. The final implementation uses
explicit completion callbacks and completes both presentation and tap callbacks
on the main actor. The same background/terminated flow then passed.

The generic Simulator SDK build and harness Ruff check passed. The final build
also checks the last permission-error copy change. No backend files changed; no
backend pytest suite or physical-device delivery is claimed. No independent
agent was delegated: this worker's brief explicitly requires doing the work alone.


The branch was subsequently rebased onto `ios-app` at `4317aef4`. Integration
preserves the newer full More tab, deployment capsule, conversation smoke entry,
and title-case Settings heading. Review notification taps with a ticket now open
Workspace, matching the updated Review screen. The corresponding UI expectation
was updated; the rebase was verified by a compile-only build as requested by
firstmate, without repeating Simulator interaction tests. The earlier Simulator
evidence above belongs to the pre-rebase navigation shell.
