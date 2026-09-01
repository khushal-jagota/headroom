/** Speaking into the composer, from the first tap to the words landing in the draft.
 *
 * The whole voice flow is one small machine with four resting places — idle, recording,
 * transcribing, failed — and this module is that machine, framework-free so it can be
 * driven in tests. The Svelte composer only draws whatever state it is told and forwards
 * taps back in; the browser machinery (getUserMedia, MediaRecorder, the wake lock, the
 * interval, the fetch) all arrives through injectable dependencies with real defaults.
 *
 * Nothing spoken is ever lost silently: a failed transcription keeps the browser Blob,
 * retry uploads it again, and only an explicit cancel throws it away.
 */

import { CONVERSATION_BASE } from "./wire";

export type VoiceCaptureState =
  | { phase: "idle" }
  | { phase: "recording"; elapsedMs: number }
  | { phase: "transcribing"; keptMs: number }
  | { phase: "failed"; keptMs: number };

export type VoiceTranscriptionBody = { audio: string; media_type: string };

export type VoiceTranscriptionResult = {
  transcript: string;
};

/** A transcription request that the server refused. */
export class VoiceTranscriptionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "VoiceTranscriptionError";
  }
}

export async function postVoiceTranscription(
  body: VoiceTranscriptionBody,
  signal?: AbortSignal
): Promise<VoiceTranscriptionResult> {
  let response: Response;
  try {
    response = await fetch(
      `${CONVERSATION_BASE}/voice-transcriptions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal
      }
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new VoiceTranscriptionError("The server could not be reached.");
  }
  const raw = await response.text();
  if (response.ok) {
    try {
      const parsed = JSON.parse(raw) as { transcript?: unknown };
      return {
        transcript: typeof parsed.transcript === "string" ? parsed.transcript : ""
      };
    } catch {
      throw new VoiceTranscriptionError("The server sent something that is not JSON.");
    }
  }
  throw failureFrom(raw, response.status);
}

function failureFrom(raw: string, status: number): VoiceTranscriptionError {
  let message = `The server answered ${status}.`;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      if (typeof detail === "string" && detail !== "") message = detail;
    }
  } catch {
    // Not JSON. The status is all the server said.
  }
  return new VoiceTranscriptionError(message);
}

/** m:ss, the way a person reads a short recording's length. */
export function formatVoiceTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

/** Whether this browser can record voice at all. */
export function voiceCaptureSupported(): boolean {
  return (
    typeof MediaRecorder !== "undefined"
    && typeof navigator !== "undefined"
    && typeof navigator.mediaDevices?.getUserMedia === "function"
  );
}

export function voiceCaptureAvailable(
  supported: boolean,
  transcriptAccepted: boolean
): boolean {
  return supported && transcriptAccepted;
}

export function voiceFirstComposer(input: {
  available: boolean;
  coarsePointer: boolean;
  active: boolean;
  keyboardPreferred: boolean;
  disabled: boolean;
  empty: boolean;
}): boolean {
  return (
    input.available
    && input.coarsePointer
    && !input.active
    && !input.keyboardPreferred
    && !input.disabled
    && input.empty
  );
}

// --- the injectable browser edges -----------------------------------------------------------

export type VoiceRecorderLike = {
  mimeType?: string;
  start: () => void;
  stop: () => void;
  ondataavailable: ((event: { data: Blob }) => void) | null;
  onstop: (() => void) | null;
};

export type VoiceStreamLike = {
  getTracks: () => { stop: () => void }[];
};

export type VoiceCaptureDeps = {
  getStream: () => Promise<VoiceStreamLike>;
  makeRecorder: (stream: VoiceStreamLike) => VoiceRecorderLike;
  transcribe: (
    body: VoiceTranscriptionBody,
    signal: AbortSignal
  ) => Promise<VoiceTranscriptionResult>;
  now: () => number;
  /** Start the elapsed-time ticker; returns the stop. */
  startTicker: (tick: () => void) => () => void;
  /** Hold a screen wake lock; returns the release. */
  acquireWakeLock: () => () => void;
};

function defaultMakeRecorder(stream: VoiceStreamLike): VoiceRecorderLike {
  const webmSupported =
    typeof MediaRecorder.isTypeSupported === "function"
    && MediaRecorder.isTypeSupported("audio/webm");
  return new MediaRecorder(
    stream as unknown as MediaStream,
    webmSupported ? { mimeType: "audio/webm" } : undefined
  ) as unknown as VoiceRecorderLike;
}

/** The screen stays awake while someone is speaking to it. Best-effort: a browser without
 *  the lock records fine, and losing the lock to a tab switch re-asks on return. */
function defaultAcquireWakeLock(): () => void {
  let released = false;
  let sentinel: { release?: () => Promise<void> } | null = null;
  const ask = (): void => {
    navigator.wakeLock
      ?.request("screen")
      .then((lock) => {
        if (released) void lock.release?.();
        else sentinel = lock;
      })
      .catch(() => {});
  };
  const onVisibility = (): void => {
    if (document.visibilityState === "visible" && !released) ask();
  };
  ask();
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    released = true;
    document.removeEventListener("visibilitychange", onVisibility);
    void sentinel?.release?.()?.catch(() => {});
  };
}

function defaultDeps(): VoiceCaptureDeps {
  return {
    getStream: () => navigator.mediaDevices.getUserMedia({ audio: true }),
    makeRecorder: defaultMakeRecorder,
    transcribe: (body, signal) => postVoiceTranscription(body, signal),
    now: () => Date.now(),
    startTicker: (tick) => {
      const timer = setInterval(tick, 250);
      return () => clearInterval(timer);
    },
    acquireWakeLock: defaultAcquireWakeLock
  };
}

async function blobToBase64(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = "";
  const CHUNK = 0x8000;
  for (let at = 0; at < bytes.length; at += CHUNK) {
    binary += String.fromCharCode(...bytes.subarray(at, at + CHUNK));
  }
  return btoa(binary);
}

// --- the machine ----------------------------------------------------------------------------

export type VoiceCapture = {
  state: () => VoiceCaptureState;
  /** Ask for the microphone and start recording. A refusal (no permission, no device)
   *  leaves the machine idle. */
  startRecording: () => Promise<void>;
  /** Stop recording and hand what was said to the transcriber. */
  stopRecording: () => void;
  /** Whatever is underway, undone: recording discarded, transcription aborted, a kept
   *  failure thrown away. Always lands on idle. */
  cancel: () => void;
  /** From a failure, run the transcription again from what is kept. */
  retry: () => void;
  dispose: () => void;
};

export function createVoiceCapture(
  options: {
    onState: (state: VoiceCaptureState) => void;
    /** The transcript, trimmed, after a transcription lands — "" when the recording came
     *  back as nothing, which the caller treats as a no-op. */
    onTranscript: (transcript: string) => void;
  },
  overrides: Partial<VoiceCaptureDeps> = {}
): VoiceCapture {
  const deps: VoiceCaptureDeps = { ...defaultDeps(), ...overrides };
  let state: VoiceCaptureState = { phase: "idle" };
  let stream: VoiceStreamLike | null = null;
  let recorder: VoiceRecorderLike | null = null;
  let chunks: Blob[] = [];
  let startedAt = 0;
  let recordingCancelled = false;
  let stopTicker: (() => void) | null = null;
  let releaseWakeLock: (() => void) | null = null;
  let kept: {
    blob: Blob;
    mediaType: string;
    keptMs: number;
  } | null = null;
  let abort: AbortController | null = null;

  function setState(next: VoiceCaptureState): void {
    state = next;
    options.onState(next);
  }

  function stopTracks(): void {
    for (const track of stream?.getTracks() ?? []) track.stop();
    stream = null;
  }

  function endRecordingMachinery(): void {
    stopTicker?.();
    stopTicker = null;
    releaseWakeLock?.();
    releaseWakeLock = null;
  }

  async function startRecording(): Promise<void> {
    if (state.phase !== "idle") return;
    let opened: VoiceStreamLike;
    try {
      opened = await deps.getStream();
    } catch {
      return; // The microphone was refused; there is nothing to record.
    }
    if (state.phase !== "idle") {
      for (const track of opened.getTracks()) track.stop();
      return;
    }
    stream = opened;
    chunks = [];
    recordingCancelled = false;
    recorder = deps.makeRecorder(opened);
    recorder.ondataavailable = (event) => {
      if (event.data) chunks.push(event.data);
    };
    recorder.onstop = () => onRecorderStopped();
    recorder.start();
    startedAt = deps.now();
    setState({ phase: "recording", elapsedMs: 0 });
    releaseWakeLock = deps.acquireWakeLock();
    stopTicker = deps.startTicker(() => {
      if (state.phase === "recording") {
        setState({ phase: "recording", elapsedMs: deps.now() - startedAt });
      }
    });
  }

  function stopRecording(): void {
    if (state.phase !== "recording" || recorder === null) return;
    const keptMs = deps.now() - startedAt;
    endRecordingMachinery();
    recordingCancelled = false;
    setState({ phase: "transcribing", keptMs });
    recorder.stop();
  }

  function onRecorderStopped(): void {
    stopTracks();
    const taken = chunks;
    chunks = [];
    recorder = null;
    if (recordingCancelled) return;
    if (state.phase !== "transcribing") return;
    const mediaType = taken[0]?.type || "audio/webm";
    kept = {
      blob: new Blob(taken, { type: mediaType }),
      mediaType,
      keptMs: state.keptMs
    };
    void transcribeKept();
  }

  async function transcribeKept(): Promise<void> {
    const holding = kept;
    if (holding === null) {
      kept = null;
      setState({ phase: "idle" });
      return;
    }
    if (state.phase !== "transcribing") {
      setState({ phase: "transcribing", keptMs: holding.keptMs });
    }
    const controller = new AbortController();
    abort = controller;
    try {
      const body = {
        audio: await blobToBase64(holding.blob),
        media_type: holding.mediaType
      };
      const answer = await deps.transcribe(body, controller.signal);
      if (controller.signal.aborted) return;
      abort = null;
      kept = null;
      setState({ phase: "idle" });
      options.onTranscript(answer.transcript.trim());
    } catch (error) {
      if (controller.signal.aborted) return;
      abort = null;
      setState({ phase: "failed", keptMs: holding.keptMs });
    }
  }

  function cancel(): void {
    if (state.phase === "recording") {
      recordingCancelled = true;
      endRecordingMachinery();
      recorder?.stop();
      stopTracks();
      setState({ phase: "idle" });
      return;
    }
    if (state.phase === "transcribing") {
      abort?.abort();
      abort = null;
      recordingCancelled = true; // a still-flushing recorder must not restart the flow
      kept = null;
      setState({ phase: "idle" });
      return;
    }
    if (state.phase === "failed") {
      kept = null;
      setState({ phase: "idle" });
    }
  }

  function retry(): void {
    if (state.phase !== "failed" || kept === null) return;
    setState({ phase: "transcribing", keptMs: kept.keptMs });
    void transcribeKept();
  }

  return {
    state: () => state,
    startRecording,
    stopRecording,
    cancel,
    retry,
    dispose: () => {
      cancel();
      endRecordingMachinery();
      stopTracks();
    }
  };
}
