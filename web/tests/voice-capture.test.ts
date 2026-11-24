import { describe, expect, it, vi } from "vitest";
import { createVoiceCapture, VOICE_LEVEL_HISTORY, type VoiceCaptureDeps, type VoiceRecorderLike, type VoiceTranscriptionResult } from "../src/lib/conversation/voiceCapture";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function fixture() {
  const uploads: { body: Parameters<VoiceCaptureDeps["transcribe"]>[0]; signal: AbortSignal; answer: ReturnType<typeof deferred<VoiceTranscriptionResult>> }[] = [];
  const transcript = vi.fn();
  const stopTrack = vi.fn();
  const stopTicker = vi.fn();
  const releaseWakeLock = vi.fn();
  const recorder: VoiceRecorderLike = {
    mimeType: "audio/webm", start: vi.fn(), ondataavailable: null, onstop: null,
    stop: () => {
      recorder.ondataavailable?.({ data: new Blob(["recorded words"], { type: "audio/webm" }) });
      recorder.onstop?.();
    }
  };
  const getStream = vi.fn(async () => ({ getTracks: () => [{ stop: stopTrack }] }));
  const capture = createVoiceCapture({ onState: vi.fn(), onTranscript: transcript }, {
    getStream, makeRecorder: () => recorder, now: () => 1000,
    startTicker: () => stopTicker, acquireWakeLock: () => releaseWakeLock,
    transcribe: (body, signal) => {
      const answer = deferred<VoiceTranscriptionResult>();
      uploads.push({ body, signal, answer });
      return answer.promise;
    }
  });
  const finishRecording = async () => {
    await capture.startRecording();
    capture.stopRecording();
    await vi.waitFor(() => expect(uploads).toHaveLength(1));
  };
  return { capture, getStream, uploads, transcript, stopTrack, stopTicker, releaseWakeLock, finishRecording };
}

describe("voice capture refusal and abandonment", () => {
  it("leaves a refused microphone idle and permits a deliberate retry", async () => {
    const f = fixture();
    f.getStream.mockRejectedValueOnce(new Error("Permission denied"));
    await f.capture.startRecording();
    expect(f.capture.state()).toEqual({ phase: "idle" });
    expect(f.uploads).toEqual([]);
    expect(f.transcript).not.toHaveBeenCalled();
    await f.capture.startRecording();
    expect(f.capture.state()).toEqual({ phase: "recording", elapsedMs: 0 });
    f.capture.cancel();
    expect(f.stopTrack).toHaveBeenCalledTimes(1);
  });

  it("retains refused audio and retries the identical payload without another microphone read", async () => {
    const f = fixture();
    await f.finishRecording();
    f.uploads[0].answer.reject(new Error("Transcription refused"));
    await vi.waitFor(() => expect(f.capture.state().phase).toBe("failed"));
    expect(f.transcript).not.toHaveBeenCalled();
    f.capture.retry();
    await vi.waitFor(() => expect(f.uploads).toHaveLength(2));
    expect(f.uploads[1].body).toEqual({ audio: "cmVjb3JkZWQgd29yZHM=", media_type: "audio/webm" });
    expect(f.uploads[1].body).toEqual(f.uploads[0].body);
    expect(f.getStream).toHaveBeenCalledTimes(1);
    f.uploads[1].answer.resolve({ transcript: "  Kept draft words  " });
    await vi.waitFor(() => expect(f.transcript).toHaveBeenCalledExactlyOnceWith("Kept draft words"));
    expect(f.capture.state()).toEqual({ phase: "idle" });
    expect(f.stopTrack).toHaveBeenCalledTimes(1);
    expect(f.stopTicker).toHaveBeenCalledTimes(1);
    expect(f.releaseWakeLock).toHaveBeenCalledTimes(1);
  });

  it.each(["cancel", "dispose"] as const)("%s rejects a late transcript without poisoning the next recording", async (action) => {
    const f = fixture();
    await f.finishRecording();
    f.capture[action]();
    expect(f.uploads[0].signal.aborted).toBe(true);
    expect(f.capture.state()).toEqual({ phase: "idle" });
    f.uploads[0].answer.resolve({ transcript: "Must not enter the draft" });
    await Promise.resolve();
    expect(f.transcript).not.toHaveBeenCalled();
    if (action === "cancel") {
      await f.capture.startRecording();
      expect(f.capture.state().phase).toBe("recording");
      f.capture.cancel();
    }
  });
});

describe("voice level bars", () => {
  it("records recent loudness while a meter listens, and closes it when the take ends", async () => {
    let tick: () => void = () => {};
    const close = vi.fn();
    const loudness = [0.2, 0.8];
    const recorder: VoiceRecorderLike = { start: vi.fn(), stop: vi.fn(), ondataavailable: null, onstop: null };
    const capture = createVoiceCapture({ onState: vi.fn(), onTranscript: vi.fn() }, {
      getStream: async () => ({ getTracks: () => [] }),
      makeRecorder: () => recorder,
      now: () => 0,
      startTicker: (next) => { tick = next; return () => {}; },
      acquireWakeLock: () => () => {},
      openMeter: () => ({ read: () => loudness.shift() ?? 0, close })
    });
    await capture.startRecording();
    expect(capture.state()).toEqual({ phase: "recording", elapsedMs: 0, levels: [] });
    tick();
    tick();
    expect(capture.state()).toMatchObject({ levels: [0.2, 0.8] });
    for (let index = 0; index < VOICE_LEVEL_HISTORY + 5; index += 1) tick();
    const state = capture.state();
    expect(state.phase === "recording" ? state.levels?.length : -1).toBe(VOICE_LEVEL_HISTORY);
    capture.cancel();
    expect(close).toHaveBeenCalledTimes(1);
  });
});
