import { describe, expect, it } from "vitest";

import {
  createVoiceCapture,
  VoiceTranscriptionError,
  type VoiceCaptureDeps,
  type VoiceCaptureState,
  type VoiceRecorderLike,
  type VoiceStreamLike,
  type VoiceTranscriptionBody,
  type VoiceTranscriptionResult
} from "../src/lib/conversation/voiceCapture";

/** A recorder that hands one webm chunk back when stopped, the way MediaRecorder does. */
class FakeRecorder implements VoiceRecorderLike {
  mimeType: string;
  started = false;
  stopped = false;
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(private readonly chunkMediaType = "audio/webm") {
    this.mimeType = chunkMediaType;
  }

  start(): void {
    this.started = true;
  }

  stop(): void {
    this.stopped = true;
    this.ondataavailable?.({
      data: new Blob(["spoken words"], { type: this.chunkMediaType })
    });
    this.onstop?.();
  }
}

type Harness = {
  capture: ReturnType<typeof createVoiceCapture>;
  states: VoiceCaptureState[];
  transcripts: string[];
  recorders: FakeRecorder[];
  stream: { stopped: number };
  requests: { body: VoiceTranscriptionBody; signal: AbortSignal }[];
  wakeLocks: { held: number; released: number };
  tick: () => void;
  advance: (ms: number) => void;
};

function makeHarness(
  transcribe: (
    body: VoiceTranscriptionBody,
    signal: AbortSignal
  ) => Promise<VoiceTranscriptionResult>,
  chunkMediaType = "audio/webm"
): Harness {
  let clock = 0;
  const states: VoiceCaptureState[] = [];
  const transcripts: string[] = [];
  const recorders: FakeRecorder[] = [];
  const stream = { stopped: 0 };
  const requests: { body: VoiceTranscriptionBody; signal: AbortSignal }[] = [];
  const wakeLocks = { held: 0, released: 0 };
  let ticker: (() => void) | null = null;
  const streamLike: VoiceStreamLike = {
    getTracks: () => [{ stop: () => (stream.stopped += 1) }]
  };
  const deps: Partial<VoiceCaptureDeps> = {
    getStream: () => Promise.resolve(streamLike),
    makeRecorder: () => {
      const recorder = new FakeRecorder(chunkMediaType);
      recorders.push(recorder);
      return recorder;
    },
    transcribe: (_conversationId, body, signal) => {
      requests.push({ body, signal });
      return transcribe(body, signal);
    },
    now: () => clock,
    startTicker: (tick) => {
      ticker = tick;
      return () => (ticker = null);
    },
    acquireWakeLock: () => {
      wakeLocks.held += 1;
      return () => (wakeLocks.released += 1);
    }
  };
  const capture = createVoiceCapture(
    {
      conversationId: () => "conv-1",
      onState: (state) => states.push(state),
      onTranscript: (transcript) => transcripts.push(transcript)
    },
    deps
  );
  return {
    capture,
    states,
    transcripts,
    recorders,
    stream,
    requests,
    wakeLocks,
    tick: () => ticker?.(),
    advance: (ms) => (clock += ms)
  };
}

function settle(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

describe("voice capture machine", () => {
  it("runs idle → recording → transcribing → landed transcript", async () => {
    const harness = makeHarness(async () => ({
      transcript: "  move the retry logic  ",
      stored_file_id: "sf-1"
    }));
    expect(harness.capture.state().phase).toBe("idle");

    await harness.capture.startRecording();
    expect(harness.capture.state()).toEqual({ phase: "recording", elapsedMs: 0 });
    expect(harness.wakeLocks.held).toBe(1);

    harness.advance(41_000);
    harness.tick();
    expect(harness.capture.state()).toEqual({ phase: "recording", elapsedMs: 41_000 });

    harness.capture.stopRecording();
    expect(harness.capture.state()).toEqual({ phase: "transcribing", keptMs: 41_000 });
    expect(harness.wakeLocks.released).toBe(1);
    expect(harness.stream.stopped).toBe(1);

    await settle();
    expect(harness.capture.state().phase).toBe("idle");
    // The transcript lands trimmed, as one person-made change to the draft.
    expect(harness.transcripts).toEqual(["move the retry logic"]);
    const sent = harness.requests[0]?.body;
    expect(sent && "audio" in sent && sent.media_type).toBe("audio/webm");
  });

  it("lands an empty transcript as a no-op, still returning to idle", async () => {
    const harness = makeHarness(async () => ({ transcript: "   ", stored_file_id: null }));
    await harness.capture.startRecording();
    harness.capture.stopRecording();
    await settle();
    expect(harness.capture.state().phase).toBe("idle");
    expect(harness.transcripts).toEqual([""]);
  });

  it("uploads the codec-qualified media type emitted by a mobile recorder", async () => {
    const harness = makeHarness(
      async () => ({ transcript: "mobile words", stored_file_id: "sf-mobile" }),
      "audio/webm;codecs=opus"
    );
    await harness.capture.startRecording();
    harness.capture.stopRecording();
    await settle();

    const sent = harness.requests[0]?.body;
    expect(sent && "audio" in sent && sent.media_type).toBe("audio/webm;codecs=opus");
    expect(harness.transcripts).toEqual(["mobile words"]);
  });

  it("cancel while recording discards everything and stops the tracks", async () => {
    const harness = makeHarness(async () => {
      throw new Error("must not be called");
    });
    await harness.capture.startRecording();
    harness.capture.cancel();
    expect(harness.capture.state().phase).toBe("idle");
    expect(harness.stream.stopped).toBe(1);
    expect(harness.wakeLocks.released).toBe(1);
    expect(harness.requests).toHaveLength(0);
    expect(harness.transcripts).toHaveLength(0);
  });

  it("cancel while transcribing aborts the request and keeps nothing", async () => {
    let sawAbort = false;
    const harness = makeHarness(
      (_body, signal) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            sawAbort = true;
            reject(new DOMException("aborted", "AbortError"));
          });
        })
    );
    await harness.capture.startRecording();
    harness.capture.stopRecording();
    await settle();
    expect(harness.capture.state().phase).toBe("transcribing");
    harness.capture.cancel();
    await settle();
    expect(sawAbort).toBe(true);
    expect(harness.capture.state().phase).toBe("idle");
    // Nothing is kept: a retry from here is meaningless and does nothing.
    harness.capture.retry();
    expect(harness.capture.state().phase).toBe("idle");
    expect(harness.transcripts).toHaveLength(0);
  });

  it("failure keeps the recording and retries with the stored_file_id the 502 carried", async () => {
    let calls = 0;
    const harness = makeHarness(async () => {
      calls += 1;
      if (calls === 1) throw new VoiceTranscriptionError("upstream busted", "kept-9");
      return { transcript: "second time lucky", stored_file_id: "kept-9" };
    });
    await harness.capture.startRecording();
    harness.advance(41_000);
    harness.capture.stopRecording();
    await settle();
    expect(harness.capture.state()).toEqual({ phase: "failed", keptMs: 41_000 });

    harness.capture.retry();
    expect(harness.capture.state()).toEqual({ phase: "transcribing", keptMs: 41_000 });
    await settle();
    expect(harness.capture.state().phase).toBe("idle");
    expect(harness.transcripts).toEqual(["second time lucky"]);
    // The retry pointed at the kept server-side file rather than re-uploading.
    expect(harness.requests[1]?.body).toEqual({ stored_file_id: "kept-9" });
  });

  it("failure without a stored_file_id retries by re-uploading the kept blob", async () => {
    let calls = 0;
    const harness = makeHarness(async () => {
      calls += 1;
      if (calls === 1) throw new VoiceTranscriptionError("nothing kept upstream", null);
      return { transcript: "done", stored_file_id: null };
    });
    await harness.capture.startRecording();
    harness.capture.stopRecording();
    await settle();
    expect(harness.capture.state().phase).toBe("failed");
    harness.capture.retry();
    await settle();
    const second = harness.requests[1]?.body;
    expect(second && "audio" in second).toBe(true);
    expect(harness.transcripts).toEqual(["done"]);
  });

  it("discarding a failure lands on idle with nothing kept", async () => {
    const harness = makeHarness(async () => {
      throw new VoiceTranscriptionError("no", null);
    });
    await harness.capture.startRecording();
    harness.capture.stopRecording();
    await settle();
    expect(harness.capture.state().phase).toBe("failed");
    harness.capture.cancel();
    expect(harness.capture.state().phase).toBe("idle");
    harness.capture.retry();
    expect(harness.capture.state().phase).toBe("idle");
    expect(harness.requests).toHaveLength(1);
  });
});
