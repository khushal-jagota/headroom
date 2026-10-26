<script lang="ts">
  /** The pane the Chief and every Manager are drawn in: a document, an artifact over it,
   *  and the conversation in its own layer below.
   *
   *  The artifact lives here rather than in each pane, so a press on a link to one is
   *  claimed across the whole pane — the conversation included. That is the point: the
   *  artifact and the conversation it was named in belong on the screen together, and an
   *  artifact named in a conversation is usually some other Ticket's. */
  import { conversationDock } from "../lib/conversation/layout/dock";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import ArtifactPreview from "./ArtifactPreview.svelte";
  import { createConversationDismissal } from "../lib/conversation/dismissal";
  import { createArtifactOnThePage } from "../lib/artifactOnThePage.svelte";
  import type { ManagedFileTarget } from "../lib/filePreview";
  import type { ConversationState } from "../lib/conversation/conversationState";

  let {
    conversationState = $bindable("rest"),
    children,
    conversation,
    openedFile = null,
    onOpenFile,
    class: className = "",
    ...attributes
  }: HTMLAttributes<HTMLDivElement> & {
    conversationState?: ConversationState;
    children: Snippet;
    conversation: Snippet;
    /** The artifact a host that keeps one in the address wants shown. */
    openedFile?: ManagedFileTarget | null;
    /** How that host stores it. Without this the pane keeps its own. */
    onOpenFile?: (file: ManagedFileTarget | null) => void;
  } = $props();

  const dismissal = createConversationDismissal(() => { conversationState = "rest"; });
  const dismissOutsideConversation = dismissal.surface;

  let heldFile = $state<ManagedFileTarget | null>(null);
  const artifact = createArtifactOnThePage({
    shown: () => (onOpenFile ? openedFile : heldFile),
    keep: (file) => { if (onOpenFile) onOpenFile(file); else heldFile = file; },
    conversationState: () => conversationState,
    dismissConversation: () => { conversationState = "rest"; }
  });
</script>

<svelte:window onkeydown={artifact.closeOnEscape} />

<!-- Links keep their keyboard behavior. Enter on a link raises this same event, so the
     claim needs no key handling of its own. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div use:conversationDock use:dismissOutsideConversation class={`manager-page ${className}`} {...attributes} onclick={artifact.claimPress}>
  <div class="manager-reading">
    <main class="manager-doc">
      <div class="manager-column">{@render children()}</div>
    </main>
    {#if artifact.shown}
      <ArtifactPreview
        target={artifact.shown}
        registerArtifactFrame={dismissal.registerArtifactFrame}
        reloadSignal={artifact.reloadSignal}
        bind:element={artifact.element}
        onRefresh={artifact.refresh}
        onClose={artifact.close}
      />
    {/if}
  </div>
  <div class="conversation-layer">
    <div class="conversation-column">{@render conversation()}</div>
  </div>
</div>

<style>
  .manager-doc { scrollbar-gutter:auto; }
  .manager-column { font-family:var(--font-ui); font-size:15px; line-height:1.6; }
</style>
