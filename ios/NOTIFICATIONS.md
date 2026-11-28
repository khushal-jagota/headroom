# Native notifications

Official iPhone builds can connect to any configured Panels home.
Native push for other homes is future work. These signing and delivery steps apply
to the official publisher's test and live deployments.

## What Panels does today

The backend has durable attention edges and a Web Push delivery queue. There is
no notification history/feed API. An APNs sender now shares those attention edges
and preferences, with its own durable queue. The live socket's `change` frame is a
content-free foreground refresh signal, not a notification stream. It
cannot wake a suspended iOS app. The native app already follows it while open.

`server/notifications/contracts.ts` defines the notification catalog. Tickets,
Chief, and managers each offer the event types that apply to them.
`attention.ts` records false-to-true transitions. `logic/policy.ts` applies the
saved preferences and constructs a title, body, route and replacement tag.
`runtime.ts`, `web_push.ts`, and `apns.ts` deliver those intents to browser
subscriptions and registered native devices.
`web/src/components/settings/NotificationSettings.svelte` manages preferences and Web Push
subscriptions. An APNs token is not a Web Push subscription: it has neither a
browser endpoint nor the browser encryption keys.

| Event | Lock-screen example | Needed from the owner |
| --- | --- | --- |
| awaiting_reply | Chief of Staff / The revised proposal is ready for review. | Read or reply |
| awaiting_answer | iOS app · Needs an answer / A worker is waiting. Open Panels to answer the question. | Answer |
| awaiting_approval | iOS app / What Changes waits for your approval. | Decide on the proposal |
| assigned | iOS app · Assigned / The next stage is yours. Open Panels to complete it. | Complete the stage |
| errored | iOS launch · Error / Something failed. Open Panels to inspect the error. | Inspect and decide what to do |

These are payload examples, not generated claims about a live ticket. The sender
must use the real subject label and only assert details established by the edge.
Message alerts show the sender and up to 240 characters of plain text. Sender and
subject names have a 60-character limit. Attachment-only messages say “Sent an attachment.”
Approval alerts preserve the saved workflow stage sentence when a name is long.
The server captures this content and the original conversation when the attention
edge arises. Native and web push share the visible content. These previews leave
Panels through the notification provider. Use the system notification preview
settings to control what appears on the lock screen.

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
Message alerts also offer the standard iOS text Reply action. The action requires
device authentication. It needs no notification extension or extra entitlement.
iOS launches the app in the background for this action, including after termination.
The delegate reads the selected server without a SwiftUI scene. It refuses a reply
for another server or a payload without its original conversation and recipient.
If the app refuses a reply or cannot save it, a local alert says that the reply
was not sent and asks the user to open Panels.

The app saves the reply through the ordinary durable outbox before it sends or
completes the action. The notification identity, original message, recipient,
server, and reply text determine one outgoing identity. A repeated callback reuses
that identity. Replies preserve the original conversation and never create or select
a replacement conversation. The same outbox, transport and compatibility gate apply
to replies from a notification. The delegate allows at most 15 seconds for its
delivery attempt. An unfinished reply is sent again with the same identity the next
time the app is in the foreground; there is no other background retry. Finished
work expires after a day; a refused reply stays until the user acts on it.

Settings → Notifications exposes switches for the same event preferences as the
web. A successful save uses the server response. A failed save retains the last
confirmed value and shows an error. The app serializes preference saves to prevent
one response from overwriting a later choice.

Release builds for physical iPhones enable server registration by default. Debug
and Simulator builds retain `APNS_REGISTRATION_ENABLED=NO` and report **Server
delivery is not connected yet**. For a signed Debug build, explicitly set
`APNS_REGISTRATION_ENABLED=YES` and match `APNS_ENVIRONMENT` to its signed entitlement.
Permission alone does not prove delivery. Simulator injections bypass Apple and the server.

## Review Live Activity

The widget extension `PanelsLiveActivity` draws the Review Live Activity. The app does not start it. The server starts and updates it by push; see [Notifications](../docs/notifications.md#review-live-activity). `ReviewLiveActivityController` watches the push-to-start token and each activity's update token. `NotificationController` sends them to `PUT /api/notifications/apns/live-activity` once the device is registered. The watch starts at launch, because a push-to-start can launch the app with no scene. That launch registers the device against the selected server, as a notification reply does. A token that has not arrived yet never clears a stored one. Only an ended activity sends a null update token. The app ends an older Review activity when a newer one reports its token. A tap opens Review's summary through `panels://review`.

The extension's bundle ID is `com.khushaljagota.panels.LiveActivity`. Automatic signing creates its App ID. The server needs no new setting, because the push topic comes from the app's bundle ID. `PanelsTests/Fixtures/review-live-activity.json` holds the content state that both the server's tests and the host tests read. Apple's own delivery, and the Lock Screen and Island on a real phone, are checked by hand.

## Native registration contract

The backend now serves this endpoint:

```
PUT /api/notifications/apns/devices
Content-Type: application/json

{"device_token":"lowercase hex", "bundle_id":"com.khushaljagota.panels", "environment":"development"}
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
      "title": "iOS app",
      "body": "What Changes waits for your approval."
    },
    "sound": "default",
    "thread-id": "panels-ticket-t_42"
  },
  "server_url": "https://panels.example.com",
  "route": "/#/workspace/t_42"
}
```

A message alert adds `"category": "PANELS_MESSAGE_REPLY"` inside `aps`, plus this
sibling field. Legacy alerts without it retain their ordinary tap behavior:

```json
"reply": {
  "conversation_id": "c_original",
  "message_id": "c_original:7",
  "recipient_kind": "manager",
  "recipient_id": "m_original"
}
```

The recipient kinds are `chief`, `manager`, and `ticket`. Chief uses the ID `chief`.
The native action identifier is `PANELS_REPLY`. `message_id` identifies the original
message event. It is not the outgoing reply identity.

Keep the whole APNs payload under 4 KB. No badge is sent until the server owns a
correct authoritative count. `server_url` must be the canonical HTTPS origin the
app uses, without credentials, query or fragment. Supported routes:

- `/#/review` opens Review at its root; `/#/review?ticket=t_42` opens that ticket on top of Review,
  matching Review’s Open ticket; its current proposal is shown there. There is one pending proposal per ticket.
- `/#/workspace/t_42` opens a ticket on top of Review.
- `/#/workspace/item/m_9` opens an orchestrator at its root.
- `/#/agents/chief-of-staff` opens Chief at its root.

The last three are the existing backend policy routes. Approval senders can use
the Review route. Identifiers must be single alphanumeric/underscore/hyphen
components. Unsupported/deleted targets must remain read errors, not become
implicit actions or a different target.

The checked-in `NotificationPayloads/*.apns` files use example addresses.
Use `ios/scripts/test-notifications-simulator.py` for a loopback proof that writes
payloads for the selected test origin.

## Apple account and release work remains

The server reads these environment variables; none has a committed credential value:

| Variable | Required value |
| --- | --- |
| `PLAN_APNS_KEY_PATH` | Absolute path to the secret `.p8` signing key readable by the server |
| `PLAN_APNS_KEY_ID` | Apple's key identifier |
| `PLAN_APNS_TEAM_ID` | Apple Developer team identifier |
| `PLAN_APNS_BUNDLE_IDS` | Comma-separated exact allowed app IDs, such as `com.khushaljagota.panels`; include a separate test ID only when needed |

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

### Official publisher's live server credentials

The live service loads `$HOME/.config/panels/panels-live.env`. The launcher
passes the four APNs settings explicitly. Keep the private key in
`$HOME/.config/panels/secrets/apns.p8`, outside the app and repository. App
updates must not replace it. Only the service account needs read access.

1. Open Apple Developer → Certificates, Identifiers & Profiles → Identifiers.
2. Select `com.khushaljagota.panels` in team `FB99BBDJBA` and check that Push Notifications is enabled.
3. Open Keys, select **+**, and name the key **Panels native push**.
4. Enable Apple Push Notification service, then select **Configure**.
5. Choose the environment for the signed app: production for TestFlight, sandbox for a development profile.
6. Choose a key scope that permits `com.khushaljagota.panels`, then confirm and register the key.
7. Download this APNs key once and retain a secure backup.
8. Transfer that specific key to the VPS through a private file transfer.
9. Do not use the App Store Connect keys in `~/Downloads`.
10. Do not paste the key, provider JWT, or device token into chat or logs.

Apple requires Account Holder or Admin access for key creation. Its current key
screen offers Team Scoped and Topic Specific keys with environment configuration.
The sender uses one key configuration. Its key must permit the environment and
topic of the registered device. Do not assume that a newly created key permits both environments.
See [Apple's key creation instructions](https://developer.apple.com/help/account/keys/create-a-private-key).

On the VPS, prepare the destination before the private transfer:

```sh
install -d -m 700 $HOME/.config/panels/secrets
```

After the transfer, restrict the key and edit the existing environment file privately:

```sh
chmod 600 $HOME/.config/panels/secrets/apns.p8
chmod 600 $HOME/.config/panels/panels-live.env
```

Preserve the existing settings. Add these entries with the real key ID from Apple's key details:

```ini
PLAN_APNS_KEY_PATH=/absolute/path/to/panels/secrets/apns.p8
PLAN_APNS_KEY_ID=REPLACE_WITH_APNS_KEY_ID
PLAN_APNS_TEAM_ID=FB99BBDJBA
PLAN_APNS_BUNDLE_IDS=com.khushaljagota.panels
```

Keep the deployment's configured canonical HTTPS origin and owner ingress settings. Do not copy the key into an environment variable.
Chief coordinates deployment and the live restart with the owner. A Worker must not
restart or reload the live server. A changed file alone does not change its environment.

### Signed build on the Mac

First inspect the exact `.app` that supplied the installed build. Match its version
and build number to the iPhone's installed version. A repository setting does not
prove the settings of an installed binary. For an archive, use:

```sh
PANELS_ARCHIVE=/absolute/path/to/Panels.xcarchive
PANELS_APP="$PANELS_ARCHIVE/Products/Applications/Panels.app"
/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PANELS_APP/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PANELS_APP/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "$PANELS_APP/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :PanelsAPNsRegistrationEnabled' "$PANELS_APP/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :PanelsAPNsEnvironment' "$PANELS_APP/Info.plist"
codesign -d --entitlements :- "$PANELS_APP"
```

For TestFlight, check `YES`, `production`, the exact bundle ID, team, and signed
`aps-environment=production`. Also inspect the exported distribution app because
export can change its provisioning. If the matching installed binary disables
registration, build and install a replacement. An unknown installed binary needs
inspection before any claim that it supports native delivery.

Run the build on the Mac, from the ticket checkout. Choose an unused archive path:

```sh
sh ios/scripts/generate-project.sh
PANELS_ARCHIVE="$HOME/projects/panels/task-tmp/t_421nxrms/Panels.xcarchive"
xcodebuild -project ios/Panels.xcodeproj -scheme Panels \
  -configuration Release -destination 'generic/platform=iOS' \
  -archivePath "$PANELS_ARCHIVE" -allowProvisioningUpdates archive
```

The Release device settings select signing, production APNs, and server registration.
Use the intended Apple account in Xcode. Regenerate the provisioning profile if
it lacks Push Notifications. Inspect the archive before distribution. Use the
normal owner-approved TestFlight distribution route, then install the matching build.
No `.p8` key belongs in the archive, project, or build inputs.

### Physical delivery proof

1. Check that the server release with the launcher fix is live after Chief coordinates the restart.
2. Open the signed app against the canonical server through Tailscale.
3. Allow notifications under Settings (the gear in the Sidebar) → Notifications.
4. Check that the app reports **This device is registered for notifications**.
5. Check the server's device count, bundle ID, and environment without printing its token.
6. Enable the notification preference for the chosen attention event.
7. Put the app in the background and lock the phone.
8. Trigger one new, permitted attention edge through its normal Panels action.
9. Check the matching APNs delivery record without printing tokens or provider credentials.
10. Ask the owner to confirm that the alert arrived and its tap opened the correct destination.

Use a subject whose selected attention flag is false before the action. An old,
unchanged flag does not create a new alert. Keep unrelated preferences unchanged.
Provider acceptance, Simulator injection, and a queue row do not prove phone receipt.
Record the app version, environment, event, delivery result, and the owner's confirmation
in the ticket. If deployment, credentials, or the signed build are unavailable,
keep physical proof pending and request the required owner or Chief action.

Apple references: [APNs registration](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns),
[notification responses](https://developer.apple.com/documentation/usernotifications/unusernotificationcenterdelegate),
[APNs provider requests](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns).

## Focused verification

`NotificationTests` covers untrusted routes, cross-server taps, cold-launch taps,
and the native registration contract. Reply cases add the missing boundary from
untrusted notification metadata to a fixed recipient and conversation. A failed
local commit leaves no dispatchable reply and preserves an unrelated composer.
These tests reuse XCTest, SwiftData scratch stores, and the existing URLProtocol.
The existing lost-response test now starts through the notification outbox entry
point. It covers offline persistence, repeated callbacks after relaunch, and a lost
response resent with the same identity. This replaces the generic initiation of the
same recovery case rather than adding a duplicate transport matrix.

`PanelsReviewUITests` holds the opt-in notification review. Its distinct boundary
is an OS-delivered expanded notification through the delegate into the durable
outbox, including background and terminated app launches. Pure parser and outbox
tests cannot prove that the system exposes the Reply action or launches its callback.
The same one-off loopback host also checks the visible approval text and preference
switches. Its operation ledger records local preference writes and replies. It can
lose a reply response; after relaunch the phone sends it again with the same identity,
and the ledger shows one accepted reply.
No real backend messages or APNs registrations are enabled.

The DEBUG `--conversation-stub` launch option selects and saves the loopback server
through the ordinary `panels.baseURL` preference. A later SpringBoard launch uses
that saved selection because it does not retain XCTest launch arguments. DEBUG
notification parsing permits HTTP only for a loopback origin that exactly matches
the selected server. Release notification parsing requires HTTPS. The Panels unit
scheme also selects an unreachable loopback address to prevent live host reads.

```sh
PANELS_PUSH_TEST_OUTPUT=/tmp/panels-notification-proof \
  python3 ios/scripts/test-notifications-simulator.py --review
```

The helper requires the owned PanelsLaneB already booted. Another task sets
`PANELS_PUSH_LANE` to its own booted lane and `PANELS_PUSH_BUNDLE` to its own
task bundle. It holds the lane lock
and does not boot, erase, or shut down a Simulator. It surveys its loopback port
and stops its own host when the review exits. The current Ticket records measured
execution cost and evidence. Keep result bundles and build output outside artifacts.
