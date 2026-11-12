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
the cancellation reason before any provider request. The HTTP transport uses
Python's 60-second phase and inactivity policy, rather than a total request limit:
DNS/TCP connect and TLS connect each receive their own budget, each outgoing write
progress wait receives a budget, and received header/body chunks reset the read
budget. Each request owns a new connection, so there is no connection-pool wait.
Caller cancellation closes and joins the request and response. Redirect answers
return to the service without a second request or bearer-header replay. Both
production doors refuse test environments before process or network activity.
Provider errors preserve Python's diagnostics.

If the provider answers before the upload ends, a genuine socket or stream write
error does not discard that received HTTP answer. Write timeout and caller abort
still stop the request. Response content uses HTTPX's gzip and deflate decoding,
including raw deflate. The pinned Python environment has no optional Brotli or
Zstandard decoder, so those encodings remain unchanged on both sides. JSON bytes
select UTF-8, UTF-16, or UTF-32 from their BOM or leading-byte pattern. Malformed
bytes fail JSON parsing instead of replacement-character decoding. Python's
surrogate-pass behavior remains intact.

Multipart encoding follows HTTPX: `model` and `response_format` precede the audio
file, and a content length covers the exact bytes. A fresh random boundary uses
the same 16-byte hexadecimal form. The direct proof compares the actual body and
content length with HTTPX at a fixed synthetic boundary without network access.

The direct comparison uses synthetic bytes and recorded trim/HTTP answers. It proves
request content, key precedence, silence suppression, trim outcome interpretation,
provider failures, multipart bytes, and transcript filtering against Python. It
also compares encoded JSON responses against actual HTTPX response decoding. It
does not prove real audio decoding, ffmpeg process timeout, live HTTP phase timing,
early-response socket behavior, network access, or provider availability.
