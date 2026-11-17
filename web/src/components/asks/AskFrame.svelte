<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    thread,
    foot,
    aside
  }: {
    thread: Snippet;
    foot?: Snippet;
    aside?: Snippet;
  } = $props();
</script>

<div class="ask-frame" class:has-aside={aside !== undefined}>
  <div class="conversation-layer">
    <div class="conversation-column">
      <div class="chat-panel" data-conversation-pane data-conversation-state="opened" data-conversation-lens="focus">
        <div class="chat-thread-shell">
          <div class="chat-thread">
            <div class="chat-thread-content">
              {@render thread()}
            </div>
          </div>
        </div>
        {#if foot}<div class="ask-foot">{@render foot()}</div>{/if}
      </div>
    </div>
  </div>
  {#if aside}<div class="ask-aside">{@render aside()}</div>{/if}
</div>

<style>
  .ask-frame {
    display: flex;
    height: 100%;
    min-height: 0;
    min-width: 0;
    background: var(--surface-1);
  }
  .ask-frame :global(.conversation-layer) {
    flex: 1;
    height: auto;
    max-height: none;
    min-width: 0;
    min-height: 0;
    padding-top: 0;
  }
  .ask-frame :global(.conversation-column),
  .ask-frame :global(.chat-panel) { height: 100%; }
  .ask-foot {
    flex: none;
    min-width: 0;
    padding: 0 var(--space-3) var(--space-4);
  }
  .ask-aside {
    flex: none;
    width: min(30rem, 48%);
    min-width: 300px;
    min-height: 0;
    display: flex;
  }
  @media (max-width: 860px) {
    .ask-frame.has-aside { flex-direction: column; }
    .ask-aside { width: auto; min-width: 0; max-height: 62%; }
  }
</style>
