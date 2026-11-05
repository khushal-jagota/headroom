# Native notifications

## What Panels does today

The backend has durable attention edges and a Web Push delivery queue. There is
no notification history/feed API. An APNs sender now shares those attention edges
and preferences, with its own durable queue. `/api/changes` is a
content-free foreground refresh signal, not a notification stream. Polling it
cannot wake a suspended iOS app. The native app already follows it while open.

`src/planner/notifications/contracts.py` defines five event types. Tickets offer
all five; Chief and managers offer message, answer and error notifications.
`attention.py` records false-to-true transitions. `logic/policy.py` applies the
saved preferences and constructs a title, body, route and replacement tag.
`runtime.py`, `web_push.py`, and `apns.py` deliver those intents to browser
subscriptions and registered native devices.
`web/src/routes/NotificationsRoute.svelte` manages preferences and Web Push
subscriptions. An APNs token is not a Web Push subscription: it has neither a
browser endpoint nor the browser encryption keys.

| Event | Lock-screen example | Needed from Khushal |
| --- | --- | --- |
| awaiting_reply | Chief of Staff · Message / There is an unread message. Open Panels to read it. | Read the message |
| awaiting_answer | iOS app · Needs an answer / A worker is waiting. Open Panels to answer the question. | Answer |
| awaiting_approval | iOS app · Needs approval / A proposal is waiting. Open Panels to review it. | Decide on the proposal |
| assigned | iOS app · Assigned / The next stage is yours. Open Panels to complete it. | Complete the stage |
| errored | iOS launch · Error / Something failed. Open Panels to inspect the error. | Inspect and decide what to do |

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

The default build intentionally reports **Server delivery is not connected yet**.
`APNS_REGISTRATION_ENABLED=NO` prevents all device-registration requests. Merely
allowing notifications does not claim that background delivery works. The build
can still receive Simulator injections, which bypass the provider and APNs.

## Native registration contract

The backend now serves this endpoint:

```
PUT /api/notifications/apns/devices
Content-Type: application/json

{"device_token":"lowercase hex", "bundle_id":"app.panels.ios", "environment":"development"}
```

The endpoint returns 200 only after the device is durably registered. The app uses the same
URLSession and configured server as its API client. The server requires the owner
identity established by the configured Tailscale trusted ingress; headerless local
requests and employee claims cannot register or revoke devices. Deploy behind
Tailscale Serve, keep the backend loopback-only, and ensure ingress strips and supplies
its identity headers. A client-supplied identity header is not a login mechanism.
The endpoint validates allowed bundle IDs, even-length lowercase hex tokens and the
environment, and idempotently upserts each owner/token/bundle/environment combination.
Never treat possession of a device token as authentication. If the
native client needs additional credentials at deployment, provide them through
its API session before enabling this integration. `DELETE` at the same endpoint,
with the same JSON body, revokes only that registration and its deliveries; it returns
`{"removed": true}` or `{"removed": false}`. Revocation works even after credentials
or a bundle ID are removed. The app's adapter exposes it and attempts revocation after
an in-session permission denial. After an app restart the old token is not retained;
the explicit authenticated endpoint remains available for owner cleanup.
Changing servers rejects old-server taps but does not automatically revoke an old
registration; revoke it explicitly before multi-server use. APNs `Unregistered`,
`ExpiredToken`, `BadDeviceToken`, and `DeviceTokenNotForTopic` remove dead tokens.
A stale response cannot revoke a newer registration. Tokens and provider JWTs are
redacted from HTTP transport logs and never included in delivery errors.

The APNs delivery adapter runs alongside Web Push using the same attention edges,
preferences and policy. Browser and native deliveries have independent queue
records, delivery attempts and expiry cleanup; a browser success cannot consume
the native delivery. Only a new attention edge sends a notification. Transport errors,
429, 500 and 503 responses retry with increasing delays, for at most five attempts
within a five-minute lifetime. Other provider rejections stop that delivery.
The endpoint matches the registration's development or production environment;
`apns-topic` is its exact bundle ID, `apns-push-type` is `alert`, and priority is 10.
The subject replacement tag is hashed to a 64-byte collapse ID. Expiration is five
minutes after queueing. One invalid token does not block another device.

Payload (custom fields are siblings of `aps`):

```json
{
  "aps": {
    "alert": {
      "title": "iOS app · Needs approval",
      "body": "A proposal is waiting. Open Panels to review it."
    },
    "sound": "default",
    "thread-id": "panels-ticket-t_42"
  },
  "server_url": "https://panels.example.com",
  "route": "/#/workspace/t_42"
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

The server reads these environment variables; none has a committed credential value:

| Variable | Required value |
| --- | --- |
| `PLAN_APNS_KEY_PATH` | Absolute path to the secret `.p8` signing key readable by the server |
| `PLAN_APNS_KEY_ID` | Apple's key identifier |
| `PLAN_APNS_TEAM_ID` | Apple Developer team identifier |
| `PLAN_APNS_BUNDLE_IDS` | Comma-separated exact allowed app IDs, such as `app.panels.ios`; include a separate test ID only when needed |

Native sending quietly skips network access when these inputs or a canonical HTTPS
origin are absent. Partial credentials disable registration with 503. A complete but
unreadable/invalid key produces a bounded delivery failure, not a successful push.
Provider JWTs use ES256 and refresh after 50 minutes. Restart the backend when
changing credentials or allowed topics, so its connection and JWT cache are renewed.

Owner authentication and the payload origin use the existing
`PLAN_TRUSTED_INGRESS_PROVIDER=tailscale`, `PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN`, and
`PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN` settings. The origin must exactly identify
the HTTPS Panels server selected in the app, without user credentials, a path,
query or fragment. The endpoint must sit behind the verified owner access boundary.

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
   then build with the explicit Xcode override `APNS_REGISTRATION_ENABLED=YES`.
   The checked-in default remains NO. Set `APNS_ENVIRONMENT` to the signed profile's
   environment. Registration already uses the server endpoint through the app's API session.
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
