<script lang="ts">
  import { conversationOverlay } from "../lib/conversation/layout/overlay";
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { createConversationDismissal } from "../lib/conversation/dismissal";
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import type { ConversationState } from "../lib/conversation/conversationState";

  let {
    conversationState = $bindable("rest"),
    children,
    conversation,
    readingAside,
    onDocumentClick,
    class: className = "",
    ...attributes
  }: HTMLAttributes<HTMLDivElement> & {
    conversationState?: ConversationState;
    children: Snippet;
    conversation: Snippet;
    readingAside?: Snippet<[RegisterManagedArtifactFrame]>;
    onDocumentClick?: (event: MouseEvent) => void;
  } = $props();

  const dismissal = createConversationDismissal(() => { conversationState = "rest"; });
  const dismissOutsideConversation = dismissal.surface;

</script>

<div use:conversationOverlay use:dismissOutsideConversation class={`manager-page ${className}`} {...attributes}>
  <div class="manager-reading">
    <!-- Links keep their keyboard behavior. The host only redirects plain file clicks. -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <main class="manager-doc" onclick={onDocumentClick}>
      <div class="manager-column">{@render children()}</div>
    </main>
    {@render readingAside?.(dismissal.registerArtifactFrame)}
  </div>
  <div class="conversation-layer">
    <div class="conversation-column">{@render conversation()}</div>
  </div>
</div>

<style>
  .manager-doc { scrollbar-gutter:auto; }
  .manager-column { font-family:var(--font-ui); font-size:15px; line-height:1.6; }
</style>
