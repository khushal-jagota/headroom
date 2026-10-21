<script lang="ts">
  import { onMount } from "svelte";
  import { mutateJson } from "../lib/mutate";
  import type { FeedbackNote, FeedbackPageContext } from "../lib/feedback";
  import Button from "./Button.svelte";
  import Chip from "./Chip.svelte";
  import ErrorLine from "./ErrorLine.svelte";

  const DRAFT_KEY = "panels.feedback.draft";

  let {
    context,
    openCount,
    viewHref = "#/feedback",
    onSaved,
    open = $bindable(false),
    anchor,
    focusTarget
  }: {
    context: () => FeedbackPageContext;
    openCount: number;
    viewHref?: string;
    onSaved: () => void;
    open?: boolean;
    anchor: HTMLElement | null;
    focusTarget: () => HTMLElement | null;
  } = $props();

  let attached = $state(true);
  let activeContext = $state<FeedbackPageContext>({ address: "#/workspace/chief-of-staff", label: "Chief of Staff" });
  let text = $state("");
  let saving = $state(false);
  let error = $state<unknown>(null);
  let triggerElement = $state<HTMLButtonElement | null>(null);
  let popoverElement = $state<HTMLElement | null>(null);
  let inputElement = $state<HTMLTextAreaElement | null>(null);
  let viewportBottom = $state("");

  function rememberDraft(): void {
    if (text.trim()) localStorage.setItem(DRAFT_KEY, text);
    else localStorage.removeItem(DRAFT_KEY);
  }

  function openCapture(): void {
    activeContext = context();
    attached = true;
    error = null;
    open = true;
    queueMicrotask(() => inputElement?.focus());
  }

  export function closeCapture(restoreFocus = true): void {
    if (!open) return;
    rememberDraft();
    open = false;
    if (restoreFocus) queueMicrotask(() => (focusTarget() ?? triggerElement)?.focus({ preventScroll: true }));
  }

  export function toggleCapture(trigger: HTMLButtonElement): void {
    triggerElement = trigger;
    if (open) closeCapture();
    else openCapture();
  }

  function changed(): void {
    rememberDraft();
    if (error) error = null;
  }

  async function save(): Promise<void> {
    const note = text.trim();
    if (!note || saving) return;
    saving = true;
    error = null;
    const body: Record<string, string> = { text: note };
    if (attached) {
      body.page_address = activeContext.address;
      body.page_label = activeContext.label;
    }
    try {
      await mutateJson<FeedbackNote>("/api/feedback", { method: "POST", body });
      text = "";
      localStorage.removeItem(DRAFT_KEY);
      closeCapture();
      onSaved();
    } catch (caught) {
      error = caught;
    } finally {
      saving = false;
    }
  }

  function inputKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void save();
    }
  }

  function windowPointerDown(event: PointerEvent): void {
    if (!open) return;
    const target = event.target as Node;
    if (!popoverElement?.contains(target) && !triggerElement?.contains(target)) closeCapture();
  }

  function windowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && open) {
      event.preventDefault();
      closeCapture();
    }
  }

  function syncVisualViewport(): void {
    const viewport = window.visualViewport;
    if (!viewport) {
      viewportBottom = "";
      return;
    }
    viewportBottom = `${Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop)}px`;
  }

  let anchorLeft = $state(0);
  let anchorBottom = $state(0);
  $effect(() => {
    const element = anchor;
    if (!element) return;
    const measure = () => {
      const rect = element.getBoundingClientRect();
      anchorLeft = rect.left;
      anchorBottom = window.innerHeight - rect.top;
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    window.addEventListener("resize", measure);
    measure();
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  });
  onMount(() => {
    text = localStorage.getItem(DRAFT_KEY) || "";
    syncVisualViewport();
    window.visualViewport?.addEventListener("resize", syncVisualViewport);
    window.visualViewport?.addEventListener("scroll", syncVisualViewport);
    return () => {
      window.visualViewport?.removeEventListener("resize", syncVisualViewport);
      window.visualViewport?.removeEventListener("scroll", syncVisualViewport);
    };
  });
</script>

<svelte:window onpointerdown={windowPointerDown} onkeydown={windowKeydown} />

<div class="fb-anchor" class:fb-open={open} style:--feedback-viewport-bottom={viewportBottom || undefined} style:--feedback-anchor-left={`${anchorLeft}px`} style:--feedback-anchor-bottom={`${anchorBottom}px`}>
  {#if open}
    <button type="button" class="fb-scrim" aria-label="Close feedback" tabindex="-1" onclick={() => closeCapture()}></button>
  {/if}
  {#if open}
    <div bind:this={popoverElement} class="fb-pop" id="feedback-capture" role="dialog" aria-label="Add feedback" data-feedback-capture>
      <div class="sheet-grab" aria-hidden="true"></div>
      <div class="fb-pop-head">
        <strong>Feedback</strong>
        <a href={viewHref} data-feedback-view onclick={() => closeCapture(false)}>{openCount ? `${openCount} open →` : "Open list →"}</a>
      </div>
      <textarea
        bind:this={inputElement}
        class="in fb-input"
        rows="3"
        enterkeyhint="done"
        placeholder="What bugged you?"
        aria-label="Feedback note"
        data-feedback-input
        bind:value={text}
        oninput={changed}
        onkeydown={inputKeydown}
      ></textarea>
      <div class="fb-context">
        {#if attached}
          <Chip value={activeContext.label} />
          <button type="button" class="fb-x" aria-label="Do not attach this page" data-feedback-detach onclick={() => { attached = false; inputElement?.focus(); }}>×</button>
        {:else}
          <button type="button" class="fb-attach" data-feedback-attach onclick={() => { attached = true; inputElement?.focus(); }}>+ Attach this page</button>
        {/if}
      </div>
      {#if error}<ErrorLine error={{ code: "not_saved", message: "Not saved. Your note is still here." }} />{/if}
      <div class="fb-foot">
        <span class="fb-hint"><kbd>↩</kbd> save · <kbd>⇧↩</kbd> new line</span>
        <span class="spacer"></span>
        <Button variant="primary" class="fb-save" disabled={saving || !text.trim()} data-feedback-save onclick={() => void save()}>
          {error ? "Try again" : "Save"}
        </Button>
      </div>
    </div>
  {/if}
</div>
