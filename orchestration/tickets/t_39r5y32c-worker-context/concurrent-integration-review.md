# Concurrent chat-image integration review

Codex reviewed the live chat-image work against the accepted worker-context boundary.

## Findings to preserve during integration

1. `image.attach` happens before `prompt.submit`; pending context must be acknowledged only after submit succeeds. Attach or submit failure must retain context.
2. Both `_submit_and_drain` and `_stream_prompt` need context preparation and direct tests.
3. Preserve the concurrent `image_path` adapter/signature changes when threading worker identity; fix existing test-double and `RealGatewayAdapter` signature drift rather than overwriting it.
4. Keep chat-image visible Markdown/model cue separation intact: pending context changes only the Hermes text, never the Panels-visible line.
5. Extend image attach/submit failure coverage to assert pending context retention, including detach after a failed submit.

These findings constrain serial integration; they do not change the worker-context design.
