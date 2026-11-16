# Native iOS compatibility

The native app uses the same server as the browser. The server accepts a range of app builds so that a server update does not silently break an older phone.

Every native request identifies its build. One server boundary checks that build before a read, action, or stream reaches its endpoint. An unsupported app asks for an update from TestFlight. The app keeps unsent drafts and stops queued actions until a supported app replaces it. Network failures keep their separate retry behavior.

The minimum build is part of the server source. A check before publication requires a fresh compatibility review when backend code changes. The review names the oldest supported build and gives the evidence, or explains the break that requires a higher minimum. This check forces a decision but does not replace a signed-device check.

The app also needs the owner cookie. The server sets it when the web app page loads, and without it a request can only read. The app loads that page once, before its first request to a server, and keeps the cookie. It does this again when the server address changes. If the load fails, requests go on and the app tries again with the next one. Build 6 is the first with this step, so it is the minimum build. Older builds cannot write and are asked to update from TestFlight.

Legacy apps that send no build identity cannot use this protection. The first build with the gate must reach the phone before later server releases can reliably refuse it.

The code lives in `src/planner/ios_compatibility/` and `ios/Panels/Core/Networking/`. The [release procedure](../ops/ios/compatibility.md) explains the checks and the review record.

_Last verified: 2026-11-15._
