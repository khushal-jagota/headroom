# Native compatibility review

Before publication, check the oldest supported iOS build against the candidate server. Check native reads, an action, conversation output, reconnect, and an unsupported build. Confirm that unsupported requests cannot execute an action. Confirm that drafts survive an update-required response.

The gate uses `X-Panels-iOS-Build`. Native HTTP and SSE requests receive HTTP 426 with `error.code=ios_update_required`. The app presents “Update Panels from TestFlight”. WebSocket handshakes use the same refusal when the server supports denial responses, or close with code 4406. The build header is not authentication.

The server floor lives in `src/planner/ios_compatibility/policy.py`. If the candidate breaks the oldest build, raise `MINIMUM_SUPPORTED_BUILD`. Coordinate a supported TestFlight build before release. A headerless legacy build cannot receive a reliable build-specific refusal.

Record the decision in `ios/compatibility-review.json`:

- `oldest_supported_build`: the floor from the policy.
- `decision`: `compatible` or `minimum_raised`.
- `evidence`: concrete check results, or the incompatible change that requires the increase. Name any untested device behavior.
- `contract_sha256`: output from `python3 scripts/check_ios_compatibility.py --digest` after the final contract edit.

Run `python3 scripts/check_ios_compatibility.py`. The check also runs on pull requests and before publication. A stale record fails the check. The digest covers all backend Python source except packaged skills. This includes serializers and business logic because they can change native behavior without a route edit. Unrelated backend changes still need an explicit no-impact review.

Do not treat a successful digest check as device evidence. A Mac with Xcode and a signed TestFlight build must check the app against the reachable server before the owner declares live readiness.
