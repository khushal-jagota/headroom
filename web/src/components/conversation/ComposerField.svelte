<script lang="ts">
  /** The composer's field, for every place outside a conversation where a person writes.
   *
   * It is the conversation composer's own box (ConversationComposer, `.chat-box`): its fill,
   * radius and quiet focus edge, one 44px row at rest with the + at the left and the voice and
   * send controls at the right, and the same rule for the row under a long draft: once the
   * draft comes within reach of the controls, or holds a line break, the controls slide to a
   * row of their own under it (draftRows.ts decides, and draws the move). Attachments wait on
   * a strip between the draft and the controls, so attaching never moves the words being
   * written. The field owns attachment intake and the caller owns the draft and its send. The look lives in app.css, under THE COMPOSER FIELD.
   */
  import { onMount, untrack } from "svelte";
  import {
    controlsBelowTheDraft,
    createDraftLineMeasurement,
    moveBetweenDraftRows,
    DRAFT_ROWS_MOTION_MS
  } from "./composer/draftRows";
  import { formatVoiceTime, type VoiceCaptureState } from "../../lib/conversation/voiceCapture";

  import AttachmentChips from "./AttachmentChips.svelte";
  import { attachmentSurface } from "./composer/attachmentSurface";
  import ComposerAttachments from "./composer/ComposerAttachments.svelte";
  import { attachmentByteLabel } from "./composer/attachments";
  import { videoUploadAdapter } from "../../lib/conversation/videoUploads/adapter";
  import { attachmentTransfersFor, openAttachmentDraft, liveAttachmentDrafts, publishAttachmentDraft } from "../../lib/conversation/videoUploads/uploads";
  import type { PendingAttachment, UploadDestination } from "../../lib/conversation/videoUploads/contracts";
  import { type PendingConversationFile } from "../../lib/conversation/pendingFiles";
  import { type PendingConversationImage } from "../../lib/conversation/pendingImages";

  type Attributes = Record<string, string | boolean | undefined>;
  type Control = { label: string; disabled?: boolean; onclick: () => void; attributes?: Attributes };

  let {
    value = $bindable(""),
    input = $bindable(null),
    placeholder,
    label,
    disabled = false,
    maxLines = 10,
    held = null,
    inputAttributes = {},
    onkeydown,
    onsubmit,
    draftKey,
    uploadDestination,
    uploads = $bindable([]),
    images = $bindable([]),
    files = $bindable([]),
    intakeBusy = $bindable(false),
    onError,
    attachmentUnavailable,
    attachmentDisabled = false,
    record,
    send,
    voice,
    variant = "field"
  }: {
    value?: string;
    /** The writing area, for a caller that moves the cursor into it. */
    input?: HTMLTextAreaElement | null;
    placeholder: string;
    /** The writing area's accessible name, where the placeholder is not enough. */
    label?: string;
    disabled?: boolean;
    /** How many lines the field grows to before it scrolls. */
    maxLines?: number;
    /** A field that keeps its geometry after a send: never shorter than `box`, its writing
     *  area never shorter than `input` (px), and controls that went under the draft stay there,
     *  so the words area stays exactly where it was. */
    held?: { box: number; input: number } | null;
    inputAttributes?: Attributes;
    onkeydown?: (event: KeyboardEvent & { currentTarget: HTMLTextAreaElement }) => void;
    /** Opt in to "Enter sends": plain Enter and Cmd/Ctrl+Enter call this, Shift+Enter adds a
     *  line, and Enter while an input method is composing, or held down, does nothing. */
    onsubmit?: () => void;
    draftKey?: string;
    uploadDestination: UploadDestination;
    uploads?: PendingAttachment[];
    images?: PendingConversationImage[];
    files?: PendingConversationFile[];
    /** A send or decision waits until the files are read. */
    intakeBusy?: boolean;
    onError?: (error: unknown) => void;
    /** Only an exact backend restriction removes the attachment control, with this reason. */
    attachmentUnavailable?: string;
    /** A caller can suspend file intake while keeping the text field usable. */
    attachmentDisabled?: boolean;
    /** The microphone, first of the controls at the right. */
    record?: Control;
    /** The send arrow, last at the right: lit when there is something to send, a spinner
     *  while it goes, then for a moment a green tick (`result` "sent"), a red cross ("failed"), or an amber question mark ("unsure": it may not have arrived). */
    send?: Control & { ready: boolean; sending: boolean; result?: 'sent' | 'failed' | 'unsure'; allowWhileIntake?: boolean };
    /** A voice take underway: its line stands where the words go, its cancel where the + is,
     *  and its stop or retry where send is. */
    voice?: {
      state: VoiceCaptureState;
      cancel: () => void;
      stop: () => void;
      retry: () => void;
    };
    /** "reply-strip" is the one-line reply an ask shell (AskShell) carries under its own
     *  actions: no shell of its own, 48px tall, the words in the conversation's body face and
     *  its mic and send at the right. The ask's shell is its frame. */
    variant?: "field" | "reply-strip";
  } = $props();

  function keydown(event: KeyboardEvent & { currentTarget: HTMLTextAreaElement }): void {
    onkeydown?.(event);
    if (onsubmit === undefined || event.defaultPrevented || event.key !== "Enter") return;
    if (intakeBusy && !send?.allowWhileIntake) { event.preventDefault(); return; }
    if (event.shiftKey || event.altKey) return;
    // Enter that confirms an input method's text (keyCode 229) is not a send.
    if (event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!event.repeat) onsubmit();
  }

  let fileInput = $state<HTMLInputElement | null>(null);
  let dragging = $state(false);
  let intakeError = $state("");
  const localKey = $props.id();
  let registryKey = $derived(draftKey ?? localKey);
  let hasAttachments = $derived(images.length > 0 || files.length > 0 || uploads.length > 0);
  let uploadTiles = $derived(uploads.map((value) => ({ kind: "video" as const, value })));
  $effect(() => {
    const key = registryKey;
    const shared = liveAttachmentDrafts.get(key);
    untrack(() => {
      uploads = shared ?? openAttachmentDraft(key, uploads);
      const transfers = attachmentTransfersFor(key, videoUploadAdapter);
      for (const attachment of uploads) if (attachment.phase === "validating" && attachment.uploadId) void transfers.validate(attachment);
    });
  });
  $effect(() => { intakeBusy = uploads.some((attachment) => attachment.phase !== "ready"); });

  function intake(chosen: readonly File[]): void {
    if (chosen.length === 0 || disabled || attachmentDisabled || attachmentUnavailable) return;
    intakeError = "";
    onError?.(null);
    const key = registryKey;
    const transfers = attachmentTransfersFor(key, videoUploadAdapter);
    for (const file of chosen) {
      try {
        const current = liveAttachmentDrafts.get(key) ?? uploads;
        let attachment = $state(transfers.create(file, Math.max(0, ...current.map((entry) => entry.id)) + 1, uploadDestination));
        const replaced = current.filter((entry) => (entry.phase === "interrupted" || (entry.phase === "failed" && !entry.file)) && entry.fileName === file.name);
        for (const previous of replaced) transfers.remove(previous);
        const replacement = current.filter((entry) => !replaced.includes(entry));
        publishAttachmentDraft(key, [...replacement, attachment]);
        void transfers.start(attachment);
        void transfers.measure(attachment);
      } catch (problem) {
        intakeError = problem instanceof Error ? problem.message : "The attachment could not be read.";
        onError?.(problem);
      }
    }
    if (fileInput) fileInput.value = "";
  }

  function removeUpload(attachment: PendingAttachment): void {
    attachmentTransfersFor(registryKey, videoUploadAdapter).remove(attachment);
    publishAttachmentDraft(registryKey, uploads.filter((entry) => entry !== attachment));
  }
  function uploadProgress(attachment: PendingAttachment): string {
    return attachment.phase === "uploading" ? `Uploading ${Math.floor(attachment.receivedBytes / Math.max(1, attachment.byteCount) * 100)}%`
      : attachment.phase === "validating" ? "Checking file…" : attachment.phase === "ready" ? attachmentByteLabel(attachment.byteCount)
      : attachment.error || "Select the file again.";
  }

  let box = $state<HTMLDivElement | null>(null);
  let leading = $state<HTMLDivElement | null>(null);
  let trailing = $state<HTMLDivElement | null>(null);

  let voicePhase = $derived(voice?.state.phase ?? "idle");
  let speaking = $derived(voicePhase !== "idle");
  let hasLeading = $derived(Boolean(speaking || !attachmentUnavailable));
  let hasTrailing = $derived(Boolean((speaking && voicePhase !== "transcribing") || record || send));

  // --- the draft's row (ConversationComposer's placeDraftRow, for this field) ----------------
  /** Whether the controls are on their own row under the draft. Written to the box as
   *  data-composer-field-controls-below in the same step it is decided, so the layout can be
   *  read before and after it and the move drawn between the two. */
  let controlsBelow = false;
  let moving: Animation[] = [];
  let draftLine: ReturnType<typeof createDraftLineMeasurement> | null = null;
  /** On the one row, what lies between the left control (or the field's inner edge) and the
   *  right ones that is not the draft: the column gaps and the draft's own inset. */
  let oneRowGutter = 8;
  /** Whether the last placement found the field holding only its line. A field coming back to
   *  that shape (an attachment removed, a voice take ended) is placed, not moved. */
  let inOneRowShape = false;
  let placedOnce = false;
  /** Whether the controls were under the draft at the latest send of a held field. */
  let heldSeen: typeof held = null;
  let heldBelow = false;

  function roomBesideControls(field: HTMLElement, text: HTMLTextAreaElement): number | null {
    if (trailing === null || trailing.getClientRects().length === 0) return null;
    let left: number;
    if (leading !== null && leading.getClientRects().length > 0) {
      left = leading.getBoundingClientRect().right;
    } else {
      const style = getComputedStyle(field);
      left = field.getBoundingClientRect().left + Number.parseFloat(style.borderLeftWidth)
        + Number.parseFloat(style.paddingLeft);
    }
    const between = trailing.getBoundingClientRect().left - left;
    if (!controlsBelow) oneRowGutter = between - text.clientWidth;
    return between - oneRowGutter;
  }

  function setControlsBelow(field: HTMLElement, below: boolean): void {
    controlsBelow = below;
    field.toggleAttribute("data-composer-field-controls-below", below);
  }

  /** Put the controls beside the draft or under it, as the draft and its room now need. A move
   *  the person made by writing is animated; the first placement, a return to this shape and a
   *  change of width are simply placed. With attachments the draft always has its own row. */
  function placeDraftRow(animate: boolean): void {
    const field = box;
    const text = input;
    if (field !== null && hasAttachments && !controlsBelow) setControlsBelow(field, true);
    const oneRowShape = !speaking && !hasAttachments;
    if (field === null || text === null || text.hidden || !oneRowShape) {
      inOneRowShape = false;
      return;
    }
    if (!inOneRowShape) animate = false;
    inOneRowShape = true;
    const room = roomBesideControls(field, text);
    if (room === null) return;
    const style = getComputedStyle(text);
    const draftWidth = value === "" || value.includes("\n")
      ? 0
      : (draftLine ??= createDraftLineMeasurement(document)).measure(value, style);
    let next = controlsBelowTheDraft({ controlsBelow, text: value, draftWidth, roomBesideControls: room });
    // A held field keeps the shape it was sent from.
    if (heldBelow && controlsBelow) next = true;
    if (next === controlsBelow) return;
    const change = (): void => {
      setControlsBelow(field, next);
      resizeWritingArea();
    };
    if (!animate || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      for (const animation of moving) animation.cancel();
      moving = [];
      change();
      return;
    }
    const movers = [text, leading, trailing].filter(
      (element): element is HTMLTextAreaElement | HTMLDivElement =>
        element !== null && element.getClientRects().length > 0
    );
    moving = moveBetweenDraftRows(field, movers, moving, change, {
      duration: DRAFT_ROWS_MOTION_MS,
      easing: style.getPropertyValue("--motion-ease").trim() || "ease-out"
    });
  }

  /** The writing area is as tall as its lines, up to maxLines, and scrolls past that; a held
   *  field's is never shorter than it was at the send. */
  function resizeWritingArea(): void {
    const text = input;
    if (text === null || text.hidden || text.getClientRects().length === 0) return;
    const lineHeight = Number.parseFloat(getComputedStyle(text).lineHeight);
    if (!Number.isFinite(lineHeight) || lineHeight <= 0) return;
    text.style.height = "0px";
    const wantedLines = Math.max(1, Math.ceil((text.scrollHeight - 1) / lineHeight));
    const height = Math.max(Math.min(wantedLines, maxLines) * lineHeight, held?.input ?? 0);
    text.style.height = `${height}px`;
    text.style.overflowY = wantedLines > maxLines ? "auto" : "hidden";
  }

  // Effects run after Svelte has written values and hidden-state changes into the DOM.
  $effect(() => {
    value;
    voicePhase;
    hasAttachments;
    held;
    untrack(() => {
      if (held !== heldSeen) {
        heldSeen = held;
        heldBelow = held !== null && controlsBelow;
      }
      placeDraftRow(placedOnce);
      placedOnce = true;
      const heightBefore = input?.style.height;
      resizeWritingArea();
      // A line gained or lost while the box is still growing: the box takes its new height now
      // rather than at the end of the move.
      if (moving.length > 0 && input?.style.height !== heightBefore) {
        for (const animation of moving) animation.cancel();
        moving = [];
      }
    });
  });

  $effect(() => {
    const text = input;
    if (text === null) return;
    let measuredWidth = -1;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      const width = text.clientWidth;
      // The writing area's own height is an output, never another reason to resize it.
      if (width === measuredWidth) return;
      measuredWidth = width;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        placeDraftRow(false);
        resizeWritingArea();
      });
    });
    observer.observe(text);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
    };
  });

  onMount(() => () => {
    for (const animation of moving) animation.cancel();
    draftLine?.dispose();
    draftLine = null;
  });
</script>

<div
  class="composer-field"
  class:drag={dragging}
  use:attachmentSurface={{ disabled: disabled || attachmentDisabled || Boolean(attachmentUnavailable), onFiles: (chosen) => void intake(chosen), onDragChange: (active) => dragging = active }}
  class:has-leading={hasLeading}
  class:has-trailing={hasTrailing}
  class:has-attachments={hasAttachments}
  class:is-voice={speaking}
  class:is-reply-strip={variant === "reply-strip"}
  bind:this={box}
  data-composer-field
  data-composer-field-variant={variant === "reply-strip" ? "reply-strip" : undefined}
  data-composer-field-held={held !== null ? "" : undefined}
  style:min-height={held !== null ? `${held.box}px` : undefined}
>
  <input bind:this={fileInput} hidden type="file" multiple
    onchange={() => void intake(Array.from(fileInput?.files ?? []))} />

  {#if hasLeading}
    <div class="composer-field-leading" bind:this={leading}>
      {#if voice && speaking}
        <!-- The X undoes whatever voice is doing, where the + stands. -->
        <button
          type="button"
          class="chat-voice-cancel"
          data-voice-cancel
          aria-label={voicePhase === "recording"
            ? "Cancel recording"
            : voicePhase === "transcribing"
              ? "Cancel transcription"
              : "Discard recording"}
          onclick={voice.cancel}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19" /></svg>
        </button>
      {:else if !attachmentUnavailable}
        <button
          type="button"
          class="composer-field-tool"
          aria-label="Attach a file"
          data-review-attach
          disabled={disabled || attachmentDisabled}
          onclick={() => fileInput?.click()}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        </button>
      {/if}
    </div>
  {/if}

  {#if voice && speaking}
    <div class="composer-field-voice" data-composer-field-voice={voicePhase}>
      {#if voice.state.phase === "recording"}
        <span class="live-text-shimmer">recording</span>
        <span class="chat-voice-time">{formatVoiceTime(voice.state.elapsedMs)}</span>
      {:else if voice.state.phase === "transcribing"}
        <span class="live-text-shimmer">transcribing</span>
      {:else if voice.state.phase === "failed"}
        <span class="chat-voice-fail">
          transcription failed · kept
          <span class="chat-voice-time">{formatVoiceTime(voice.state.keptMs)}</span>
        </span>
      {/if}
    </div>
  {/if}

  <textarea
    class="composer-field-input"
    rows="1"
    {placeholder}
    aria-label={label}
    bind:this={input}
    bind:value
    {disabled}
    hidden={speaking}
    onkeydown={keydown}
    {...inputAttributes}
  ></textarea>

  {#if hasAttachments}
    <div class="composer-field-attachments">
      {#if images.length || files.length}<AttachmentChips bind:images bind:files disabled={disabled || attachmentDisabled} />{/if}
      <ComposerAttachments attachments={uploadTiles} inputDisabled={disabled || attachmentDisabled}
        onChooseAgain={() => fileInput?.click()} onRetry={(attachment) => void attachmentTransfersFor(registryKey, videoUploadAdapter).start(attachment)}
        onRemove={(attachment) => { if (attachment.kind === "video") removeUpload(attachment.value); }} videoProgress={uploadProgress} />
    </div>
  {/if}

  {#if hasTrailing}
    <div class="composer-field-trailing" bind:this={trailing}>
      {#if voice && speaking}
        {#if voice.state.phase === "recording"}
          <button
            type="button"
            class="chat-voice-stop"
            data-voice-stop
            aria-label="Stop recording and transcribe"
            onclick={voice.stop}
          ><span class="chat-voice-square"></span></button>
        {:else if voice.state.phase === "failed"}
          <button type="button" class="chat-voice-retry" data-voice-retry onclick={voice.retry}>retry</button>
        {/if}
      {:else}
        {#if record}
          <button
            type="button"
            class="composer-field-tool"
            aria-label={record.label}
            disabled={disabled || record.disabled}
            onclick={record.onclick}
            {...(record.attributes ?? {})}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="9" y="3" width="6" height="11" rx="3" />
              <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
            </svg>
          </button>
        {/if}
        {#if send}
          <button
            type="button"
            class="composer-field-send"
            class:on={send.ready}
            class:sent={send.result === 'sent' && !send.sending}
            class:failed={send.result === 'failed' && !send.sending}
            class:unsure={send.result === 'unsure' && !send.sending}
            aria-label={send.label}
            aria-busy={send.sending}
            disabled={send.disabled || (intakeBusy && !send.allowWhileIntake)}
            onclick={send.onclick}
            {...(send.attributes ?? {})}
          >
            {#if send.sending}
              <span class="composer-field-spinner" aria-hidden="true"></span>
            {:else if send.result === 'sent'}
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
            {:else if send.result === 'unsure'}
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 9.2a3 3 0 1 1 4.6 2.5c-1 .6-1.6 1.2-1.6 2.3M12 17.5h.01" /></svg>
            {:else if send.result === 'failed'}
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
            {:else}
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            {/if}
          </button>
        {/if}
      {/if}
    </div>
  {/if}
</div>
{#if intakeError}<p class="composer-attachment-note is-error" role="alert" data-composer-attachment-error>{intakeError}</p>{/if}
{#if attachmentUnavailable}<p class="composer-attachment-note" data-composer-attachment-unavailable>{attachmentUnavailable}</p>{/if}

<style>
  .composer-field.drag { outline: var(--focus-width) dashed var(--focus-ring); outline-offset: 2px; }
  .composer-attachment-note { margin: var(--space-1) 0 0; font-size: var(--type-sm); color: var(--tone-secondary); }
  .composer-attachment-note.is-error { color: var(--text-danger, inherit); }
  /* The reply strip: the ask's shell is its frame, so the field draws none of its own.
     One 48px row: the words at the ask's inset in the body face, the controls at the right. */
  .composer-field.is-reply-strip {
    min-height: 48px;
    padding: var(--cv-s2) var(--cv-s2) var(--cv-s2) var(--cv-s3);
    background: none;
    border: 0;
    border-radius: 0;
    box-shadow: none;
  }
  .composer-field.is-reply-strip:focus-within { border-color: transparent; }
  .composer-field.is-reply-strip > :global(.composer-field-input) {
    color: var(--cv-t1);
    font-family: var(--cv-font);
    font-size: max(var(--cv-type-ui), var(--type-input));
    line-height: 24px;
  }
  .composer-field.is-reply-strip > :global(.composer-field-input::placeholder) { color: var(--cv-placeholder); }
  .composer-field.is-reply-strip :global(.composer-field-tool) { color: var(--cv-t3); border-radius: var(--cv-r-md); }
  .composer-field.is-reply-strip :global(.composer-field-tool:hover:not(:disabled)) { color: var(--cv-t1); background: var(--cv-control); background-image: none; }
  .composer-field.is-reply-strip :global(.composer-field-tool svg) { width: 18px; height: 18px; stroke-width: 1.6; }
  .composer-field.is-reply-strip :global(.composer-field-send) { background-color: var(--cv-control); color: var(--cv-t3); }
  .composer-field.is-reply-strip :global(.composer-field-send.on) { background-color: var(--cv-primary-fill); color: var(--cv-primary-ink); }
  .composer-field :global(.composer-field-send:is(.sent, .failed, .unsure)),
  .composer-field.is-reply-strip :global(.composer-field-send:is(.sent, .failed, .unsure)) { opacity: 1; }
  .composer-field :global(.composer-field-send.sent),
  .composer-field.is-reply-strip :global(.composer-field-send.sent) { background-color: var(--accent-done); color: var(--surface-ink); }
  .composer-field :global(.composer-field-send.unsure),
  .composer-field.is-reply-strip :global(.composer-field-send.unsure) { background-color: var(--accent-attention); color: var(--surface-ink); }
  .composer-field :global(.composer-field-send.failed),
  .composer-field.is-reply-strip :global(.composer-field-send.failed) { background-color: var(--accent-error); color: var(--surface-ink); }
</style>
