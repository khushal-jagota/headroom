<script lang="ts">
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";
  import { isPrimaryPressOutsideConversation } from "../lib/conversation/dismissal";
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
    readingAside?: Snippet;
    onDocumentClick?: (event: MouseEvent) => void;
  } = $props();

  function dismissConversation(event: PointerEvent): void {
    if (isPrimaryPressOutsideConversation(event)) conversationState = "rest";
  }

</script>

<div class={`sprint-item-page ${className}`} {...attributes}>
  <div class="sprint-item-reading">
    <!-- Links keep their keyboard behavior. The host only redirects plain file clicks. -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <!-- svelte-ignore a11y_click_events_have_key_events -->
    <main class="sprint-item-doc" onpointerdowncapture={dismissConversation} onclick={onDocumentClick}>
      <div class="sprint-item-column">{@render children()}</div>
    </main>
    {@render readingAside?.()}
  </div>
  <div class="conversation-layer" onpointerdowncapture={dismissConversation}>
    <div class="conversation-column">{@render conversation()}</div>
  </div>
</div>

<style>
  .sprint-item-doc { scrollbar-gutter:auto; }
  .sprint-item-column { font-family:var(--font-ui); font-size:15px; line-height:1.6; width:min(100%, 768px); max-width:768px; margin-inline:auto; padding-inline:var(--space-5); }
</style>
