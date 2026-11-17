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
a 60-second phase and inactivity policy, rather than a total request limit:
DNS/TCP connect and TLS connect each receive their own budget, each outgoing write
progress wait receives a budget, and received header/body chunks reset the read
budget. Each request owns a new connection, so there is no connection-pool wait.
Caller cancellation closes and joins the request and response. Redirect answers
return to the service without a second request or bearer-header replay. Both
production doors refuse test environments before process or network activity.
Provider errors keep their existing diagnostics.

If the provider answers before the upload ends, a genuine socket or stream write
error does not discard that received HTTP answer. Write timeout and caller abort
still stop the request. Response content uses gzip and deflate decoding,
including raw deflate. Brotli and Zstandard encodings stay undecoded. JSON bytes
select UTF-8, UTF-16, or UTF-32 from their BOM or leading-byte pattern. Malformed
bytes fail JSON parsing instead of replacement-character decoding. Lone surrogate
escapes pass through.

In the multipart body, `model` and `response_format` precede the audio file, and
a content length covers the exact bytes. A fresh random boundary uses a 16-byte
hexadecimal form.
