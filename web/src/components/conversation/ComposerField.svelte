<script lang="ts">
  /** The composer's field, for every place outside a conversation where a person writes.
   *
   * It is the conversation composer's own box (ConversationComposer, `.chat-box`): its fill,
   * radius and quiet focus edge, one 44px row at rest with the + at the left and the voice and
   * send controls at the right, and the same rule for the row under a long draft: once the
   * draft comes within reach of the controls, or holds a line break, the controls slide to a
   * row of their own under it (draftRows.ts decides, and draws the move). Attachments wait on
   * a strip between the draft and the controls, so attaching never moves the words being
   * written. What is written, sent and spoken stays the caller's: this only draws the field and
   * hands the person's actions back. The look lives in app.css, under THE COMPOSER FIELD.
   */
  import { onMount, untrack, type Snippet } from "svelte";
  import {
    controlsBelowTheDraft,
    createDraftLineMeasurement,
    moveBetweenDraftRows,
    DRAFT_ROWS_MOTION_MS
  } from "./composer/draftRows";
  import { formatVoiceTime, type VoiceCaptureState } from "../../lib/conversation/voiceCapture";

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
    attach,
    record,
    send,
    voice,
    attachments
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
    /** The + at the left: what it adds is the caller's. */
    attach?: Control;
    /** The microphone, first of the controls at the right. */
    record?: Control;
    /** The send arrow, last at the right: lit when there is something to send, a spinner
     *  while it goes. */
    send?: Control & { ready: boolean; sending: boolean };
    /** A voice take underway: its line stands where the words go, its cancel where the + is,
     *  and its stop or retry where send is. */
    voice?: {
      state: VoiceCaptureState;
      cancel: () => void;
      stop: () => void;
      retry: () => void;
    };
    /** What is attached and waiting, on a strip under the draft. */
    attachments?: Snippet;
  } = $props();

  let box = $state<HTMLDivElement | null>(null);
  let leading = $state<HTMLDivElement | null>(null);
  let trailing = $state<HTMLDivElement | null>(null);

  let voicePhase = $derived(voice?.state.phase ?? "idle");
  let speaking = $derived(voicePhase !== "idle");
  let hasLeading = $derived(Boolean(speaking || attach));
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
    if (field !== null && attachments !== undefined && !controlsBelow) setControlsBelow(field, true);
    const oneRowShape = !speaking && attachments === undefined;
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
    attachments;
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
  class:has-leading={hasLeading}
  class:has-trailing={hasTrailing}
  class:has-attachments={attachments !== undefined}
  class:is-voice={speaking}
  bind:this={box}
  data-composer-field
  data-composer-field-held={held !== null ? "" : undefined}
  style:min-height={held !== null ? `${held.box}px` : undefined}
>

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
      {:else if attach}
        <button
          type="button"
          class="composer-field-tool"
          aria-label={attach.label}
          disabled={disabled || attach.disabled}
          onclick={attach.onclick}
          {...(attach.attributes ?? {})}
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
    {onkeydown}
    {...inputAttributes}
  ></textarea>

  {#if attachments}
    <div class="composer-field-attachments">{@render attachments()}</div>
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
            aria-label={send.label}
            aria-busy={send.sending}
            disabled={send.disabled}
            onclick={send.onclick}
            {...(send.attributes ?? {})}
          >
            {#if send.sending}
              <span class="composer-field-spinner" aria-hidden="true"></span>
            {:else}
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
            {/if}
          </button>
        {/if}
      {/if}
    </div>
  {/if}
</div>
