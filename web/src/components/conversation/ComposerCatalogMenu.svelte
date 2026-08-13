<script lang="ts">
  /** The typed entries this conversation offers for the active composer trigger. */
  import type { ComposerCatalogEntry } from "../../lib/conversation/wire";

  let {
    entries,
    activeIndex = 0,
    anyEntriesAtAll = false,
    onChoose,
    onHighlight
  }: {
    entries: readonly ComposerCatalogEntry[];
    activeIndex?: number;
    anyEntriesAtAll?: boolean;
    onChoose: (entry: ComposerCatalogEntry) => void;
    onHighlight: (index: number) => void;
  } = $props();

  let menuElement = $state<HTMLDivElement | null>(null);

  $effect(() => {
    activeIndex;
    menuElement
      ?.querySelector<HTMLElement>("[data-conversation-catalog-active]")
      ?.scrollIntoView({ block: "nearest" });
  });
</script>

<div
  class="chat-menu"
  bind:this={menuElement}
  data-conversation-catalog
  data-conversation-commands
  role="menu"
  aria-label="Composer catalog"
>
  {#if entries.length === 0}
    <div
      class="chat-menu-hd"
      data-conversation-catalog-empty
      data-conversation-commands-empty
    >
      {anyEntriesAtAll ? "No catalog entry matches that." : "No entries here."}
    </div>
  {:else}
    {#each entries as entry, index}
      <button
        type="button"
        class="chat-menu-item"
        class:on={index === activeIndex}
        role="menuitem"
        data-conversation-catalog-entry={entry.display_text}
        data-conversation-catalog-kind={entry.kind}
        data-conversation-catalog-active={index === activeIndex ? "true" : undefined}
        data-conversation-command={entry.kind === "command"
          ? entry.display_text.slice(1)
          : undefined}
        data-conversation-command-active={entry.kind === "command" && index === activeIndex
          ? "true"
          : undefined}
        onmouseenter={() => onHighlight(index)}
        onmousedown={(event) => event.preventDefault()}
        onclick={() => onChoose(entry)}
      >
        <span class="chat-menu-name">{entry.display_text}</span>
        {#if entry.argument_hint}
          <span
            class="c2-catalog-argument"
            data-conversation-catalog-argument
            data-conversation-command-argument
            >{entry.argument_hint}</span
          >
        {/if}
        <span class="chat-menu-desc">{entry.description}</span>
      </button>
    {/each}
  {/if}
</div>

<style>
  .c2-catalog-argument {
    flex: none;
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>
