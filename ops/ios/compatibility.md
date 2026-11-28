# Native compatibility review

Before publication, check the oldest supported iOS build against the candidate server. Check native reads, an action, conversation output, reconnect, and an unsupported build. Confirm that unsupported requests cannot execute an action. Confirm that drafts survive an update-required response.

The gate uses `X-Panels-iOS-Build`. Native HTTP requests receive HTTP 426 with `error.code=ios_update_required`. The app presents “Update Panels from TestFlight”. WebSocket handshakes use the same refusal when the server supports denial responses, or close with code 4406. The build header is not authentication.

The server floor lives in `server/ios_compatibility/policy.ts`. If the candidate breaks the oldest build, raise `MINIMUM_SUPPORTED_BUILD`. Coordinate a supported TestFlight build before release. A headerless legacy build cannot receive a reliable build-specific refusal.

Record the decision in `server/ios_compatibility/review.json`:

- `oldest_supported_build`: the floor from the policy.
- `decision`: `compatible` or `minimum_raised`.
- `evidence`: concrete check results, or the incompatible change that requires the increase. Name any untested device behavior.
- `contract_sha256`: the current contract digest after the final contract edit. With `server/node_modules` installed, print it from the repository root with `node -e "import('./server/ios_compatibility/check.ts').then(async (m) => console.log(await m.contract_digest('.')))"`.

The check in `server/ios_compatibility/check.ts` runs before publication and reads the review from the published commit. A stale record fails it. The digest covers all production TypeScript under `server/`, a fixed list of JSON inputs, and any driver scripts under `src/planner/`. This includes serializers and business logic because they can change native behavior without a route edit. Unrelated server changes still need an explicit no-impact review.

Do not treat a successful digest check as device evidence. A Mac with Xcode and a signed TestFlight build must check the app against the reachable server before the owner declares live readiness.

## Ticket ownership status release

The Ticket status wire value is `agent_owned`. The display label is “Agent owned”. The previous value was `agent`.

The minimum iPhone build is **11**, the first build with the renamed status. Builds 9 and 10 decode a string but put an idle owned Ticket in the wrong group. Their reads and actions receive the existing HTTP 426 `ios_update_required` response. Build 11 retains the update-required latch and draft storage behavior.

The minimum desktop bridge version is **2**. Bridge 1 also misclassifies the renamed status. Its API reads and actions receive HTTP 426 `desktop_update_required`. GET and HEAD `/api/meta` remain available so the native updater can read the new floor and request an app update. The new desktop shell reports bridge 2.

Publish the matching client builds with this server release. An old client must update before it can resume work. A signed-device check and signed desktop release remain release-owner work; CI host tests and the unsigned simulator build do not prove distribution or device readiness.

## Acceptance and delivery release

Owner sends answer `accepted` as soon as home holds the message. Delivery is reported later under the same message ID.

The minimum iPhone build is **13**, the first build that reads these answers. Builds 11 and 12 show an accepted message as sent, but a refusal that arrives after acceptance has no working Try Again there. Their reads and actions receive HTTP 426 `ios_update_required`, and the app asks for a TestFlight update and keeps its drafts.

The desktop bridge floor stays **2**. The desktop shell shows the web app that home serves and reads no send outcome.

Publish iPhone build 13 with this server release.

## Absent machine release

A send to a conversation whose machine is absent is accepted and waits, instead of HTTP 400. A machine that can never join again still refuses, with the new code `machine_unavailable`. The conversation read adds the optional `machine_waiting_reason`, and a refusal before acceptance names its cause in new refusal codes with a `reason` sentence.

The minimum iPhone build stays **13**. Build 13 ignores the new field, still shows the message as queued, and shows an unknown refusal code as words with Try Again. A newer build names the waiting machine.

## Live socket release

Web, desktop and iPhone follow live updates over the one WebSocket `/api/live`. The routes `/api/changes`, `/api/navigation/view/events`, the conversation `view/tail` and the conversation `tail` are removed.

The minimum iPhone build is **16**, the first build that uses the socket. Builds 13 to 15 followed the removed streams. Their reads and actions receive HTTP 426 `ios_update_required`, and the app asks for a TestFlight update.

The minimum desktop bridge version is **3**, the first bridge that gives the page the server address and the app's identity for its socket. Bridge 2 keeps screens that follow the removed streams. Its API reads and actions receive HTTP 426 `desktop_update_required`.

Raise both floors only after the owner's own iPhone and Mac run the socket builds. Publish iPhone build 16 and the desktop app with bridge 3 with this server release.
