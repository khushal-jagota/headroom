# ACP-05 ordered-ingress implementation report

Date: 2026-07-20  
Scope: exact-session private response epochs, SDK child wiring, and deterministic official-SDK fixtures

## Outcome

`OrderedAcpConversationIngress` now arms a private response epoch with the exact expected session ID.
A matching `session/update` selects the private sink even before the outgoing `session/load` observer
assigns a request ID. A valid update for another session stays on ordinary ingress. The request ID is
used only to freeze the load response's already-observed ordinal prefix, and the one bounded FIFO plus
raw/typed fingerprint matching remains unchanged.

`SdkAcpEmployeeChild.capture_load_session` passes the exact `LoadSessionRequest.session_id` into that
epoch before calling the official SDK. The scripted agent can now schedule ephemeral post-fork source
and candidate metadata after the fork response, hold replay until those deterministic updates are sent,
and optionally schedule metadata after the load response.

## Red evidence

The direct ingress regressions were written before the source change:

```text
$ .venv/bin/pytest -q tests/unit/test_acp_employee_child.py -k 'private_epoch_routes_matching_pre_request_update_by_exact_session_id or private_epoch_keeps_other_session_public_and_waits_for_observed_prefix or private_epoch_routes_malformed_session_identity_to_visible_rejection or response_epoch_rejects_incomplete_private_routing_shape'
FFFF                                                                     [100%]
```

Three cases failed with the exact error:

```text
TypeError: OrderedAcpConversationIngress.begin_response_consumption_epoch() got an unexpected keyword argument 'private_session_id'
```

The shape case failed because the old epoch accepted a private sink without an expected session ID:

```text
Failed: DID NOT RAISE <class 'ValueError'>
```

After the ingress source change but before the SDK child passed the load session ID, both official-SDK
subprocess regressions were red:

```text
$ .venv/bin/pytest -q tests/unit/test_acp_employee_child.py -k 'official_sdk_post_fork_updates_route_by_exact_session_without_deadlock or official_sdk_post_load_metadata_returns_to_ordinary_ingress'
FF                                                                       [100%]
```

Both exposed the missing SDK-child wiring through the exact error:

```text
ValueError: ACP private session ID must not be blank
```

## Green evidence

The four direct ordered-ingress regressions passed after exact-session routing was implemented:

```text
$ .venv/bin/pytest -q tests/unit/test_acp_employee_child.py -k 'private_epoch_routes_matching_pre_request_update_by_exact_session_id or private_epoch_keeps_other_session_public_and_waits_for_observed_prefix or private_epoch_routes_malformed_session_identity_to_visible_rejection or response_epoch_rejects_incomplete_private_routing_shape'
....                                                                     [100%]
```

The official-SDK regressions passed after `capture_load_session` supplied the expected session ID:

```text
$ .venv/bin/pytest -q tests/unit/test_acp_employee_child.py -k 'official_sdk_post_fork_updates_route_by_exact_session_without_deadlock or official_sdk_post_load_metadata_returns_to_ordinary_ingress'
..                                                                       [100%]
```

The post-fork regression records the raw SDK observer order and asserts:

```text
candidate update < outgoing candidate load request
source update < candidate load replay
```

It blocks the ordinary source sink at the single FIFO head, proves the load barrier remains pending,
then releases it and proves source traffic reached only ordinary ingress while the candidate metadata
and replay reached only the private sink. It also proves the epoch, queue, fatal state, and ordinal
barrier settle. The post-load regression proves candidate-session metadata observed after the load
response returns to ordinary ingress rather than extending the closed private prefix.

The complete owned child/ordered-ingress unit file passed after the final fixture scheduling change:

```text
$ .venv/bin/pytest -q tests/unit/test_acp_employee_child.py
.......................................                                  [100%]
```

This includes the existing overflow, capacity, raw/typed fingerprint, mismatch, shutdown, process-death,
load-barrier, and SDK lifecycle coverage.

Scoped Ruff passed:

```text
$ .venv/bin/ruff check src/planner/conversation/ordered_ingress.py src/planner/conversation/sdk_child.py tests/unit/test_acp_employee_child.py tests/support/acp_scripted_agent.py
All checks passed!
```

Configured strict Mypy passed for the owned production source:

```text
$ .venv/bin/mypy src/planner/conversation/ordered_ingress.py src/planner/conversation/sdk_child.py
Success: no issues found in 2 source files
```

Canonical `./verify`, server startup, Computer Use, generated distribution, and Codex CLI review were
not run; they remain outside this implementation lane by dispatch.
