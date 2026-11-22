<script lang="ts">
  /** A status in the thread, drawn as the conversation's one system line (B3). No card,
   *  no box, whatever the tone:
   *
   *  - a failed turn: the red dot, "This turn failed · <reason>", then Choose another model
   *    when that is the next step, and Details for the backend's own words;
   *  - a turn that stopped or was interrupted: the grey dot, "Stopped before it finished";
   *  - what became of a message (align "end"): its words under the bubble, and its actions.
   *
   *  A message that was not delivered says so inside its bubble (UserMessage), not here. */
  import { untrack, type Snippet } from "svelte";
  import type { TranscriptStatus } from "../../lib/conversation/transcript";
  import { statusReason } from "../../lib/conversation/transcript";
  import { openComposerModelPicker } from "../../lib/conversation/modelPicker";
  import SystemLine from "./SystemLine.svelte";
  import BackendWords from "./BackendWords.svelte";

  let { status, align = "start", actions: messageActions, ...attributes }: {
    status: TranscriptStatus;
    /** A line about a message stands under that message; a line about a turn starts where the thread does. */
    align?: "start" | "end";
    actions?: Snippet;
    [attribute: `data-${string}`]: string | undefined;
    role?: "status" | "alert";
  } = $props();

  // Open from the start when the backend's words are the only explanation there is.
  let detailsOpen = $state(untrack(() => status.detailOpen === true));
  let root = $state<HTMLElement | null>(null);
  const uid = $props.id();
  const detailsId = `c2-status-details-${uid}`;

  let turnFailed = $derived(align === "start" && status.tone === "error");
  let hasActions = $derived(messageActions !== undefined || status.action === "choose-model" || status.detail !== null);
  let mark = $derived(align === "end" ? null : status.tone === "error" ? "failure" as const : status.tone === "stopped" ? "dot" as const : null);
</script>

{#snippet lineActions()}
  {#if messageActions}{@render messageActions()}{/if}
  {#if status.action === "choose-model"}
    <button type="button" data-conversation-status-choose-model onclick={() => openComposerModelPicker(root)}>Choose another model</button>
  {/if}
  {#if status.detail}
    <button type="button" aria-expanded={detailsOpen} aria-controls={detailsId} data-conversation-status-details onclick={() => (detailsOpen = !detailsOpen)}>
      Details<svg class="c2-status-chevron" class:is-open={detailsOpen} viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
    </button>
  {/if}
{/snippet}

{#snippet backendWords()}
  {#if status.detail}<BackendWords id={detailsId} detail={status.detail} open={detailsOpen} />{/if}
{/snippet}

<div class="c2-status" class:c2-status--end={align === "end"} bind:this={root}>
  <SystemLine {mark} {align} actions={hasActions ? lineActions : undefined} below={status.detail ? backendWords : undefined}
    data-conversation-status-tone={status.tone} {...attributes}>
    {#if turnFailed}
      This turn failed<span aria-hidden="true">{" · "}</span><span data-conversation-status-next>{statusReason(status)}</span>
    {:else}
      {status.title}{#if status.next}<span aria-hidden="true">{" · "}</span><span data-conversation-status-next>{status.next}</span>{/if}
    {/if}
  </SystemLine>
</div>

<style>
  .c2-status { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; max-width: 100%; }
  .c2-status--end { align-self: flex-end; align-items: flex-end; }
  .c2-status-chevron {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .c2-status-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) { .c2-status-chevron { transition: none; } }
</style>
