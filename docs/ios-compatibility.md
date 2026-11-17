# Native iOS compatibility

The native app uses the same server as the browser. The server accepts a range of app builds so that a server update does not silently break an older phone.

Every native request identifies its build. One server boundary checks that build before a read, action, or stream reaches its endpoint. An unsupported app asks for an update from TestFlight. The app keeps unsent drafts and stops queued actions until a supported app replaces it. Network failures keep their separate retry behavior.

The minimum build is part of the server source. A check before publication requires a fresh compatibility review when backend code changes. The review names the oldest supported build and gives the evidence, or explains the break that requires a higher minimum. This check forces a decision but does not replace a signed-device check.

Legacy apps that send no build identity cannot use this protection. The first build with the gate must reach the phone before later server releases can reliably refuse it.

New fields reach the phone only as optional additions, so an older build keeps decoding what it reads. Backend cards carry whether Panels can sign a backend in, whether it reports allowance usage, and for each model its option descriptors and a readable name for the model it resolves to. A conversation view says whether its backend can join a running turn. `GET /api/drivers` lists each driver's label and icon and the default backend. A build that predates these fields never calls the drivers route and keeps its bundled marks and its own backend names; a backend added since shows on it as an unlabelled choice. A current build that meets an older server falls back the same way: capitalised backend names, bundled marks, Steer offered, and Usage listing the backends with a recorded allowance.

The code lives in `server/ios_compatibility/` and `ios/Panels/Core/Networking/`. The review record is `server/ios_compatibility/review.json`. The [release procedure](../ops/ios/compatibility.md) explains the checks and the review record.

_Last verified: 2026-11-17._
