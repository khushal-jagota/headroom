# Voice transcription

`transcribe_conversation_audio` returns words from browser-held audio. It does not
create a conversation or store an audio file. Configuration supplies the provider
URL, model, and key. `VoiceTranscriptionAdapters` supplies silence trim and one
OpenAI-compatible multipart request. Tests inject both adapters.

The key check precedes trim. A successful trim below 1,024 bytes returns an empty
transcript without a provider request. A missing, failed, timed-out, or empty trim
preserves the original audio. Whole-transcript silence phrases are filtered after
the provider answers. Real messages that contain those phrases remain intact.

The production adapter owns the ffmpeg process group, bounds it to 30 seconds,
and waits for the parent exit after timeout or cancellation. It closes inherited
pipes after exit, so a descendant cannot keep the request open. Timeout preserves
the original clip. Caller or server cancellation stops the group and propagates
the cancellation reason before any provider request. Its HTTP request combines
cancellation with a 60-second timeout, includes body acquisition, and does not
follow redirects. Both production doors refuse test environments before process
or network activity. Provider errors preserve Python's diagnostics.

The direct comparison uses synthetic bytes and recorded trim/HTTP answers. It proves
request content, key precedence, silence suppression, trim outcome interpretation,
provider failures, and transcript filtering against Python. It does not prove real
audio decoding, ffmpeg process timeout, network access, or provider availability.
