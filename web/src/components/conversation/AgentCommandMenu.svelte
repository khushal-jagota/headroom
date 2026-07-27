<script lang="ts">
  /** The commands this conversation's agent says a person may type at it.
   *
   * It draws what it is handed and decides nothing: which commands matched, which of them
   * is highlighted, and what to do when one is chosen. The list is exactly what the agent
   * reported — Panels adds none of its own and holds none of them back.
   *
   * Having nothing to offer is drawn rather than hidden, and the two ways of having
   * nothing are different sentences. Nothing to filter at all is one — and it says only
   * that, because an empty list is both an agent that reports no commands and one that has
   * not reported yet, and nothing here can tell those apart. Commands where none of them
   * matches what was typed is the other, and says so.
   *
   * Pressing a row leaves the cursor where it was. The box losing the cursor is what takes
   * this menu away, so a press that moved it would take the row out from under the click
   * that was choosing it.
   */
  import type { AgentCommand } from "../../lib/conversation/wire";

  let {
    commands,
    activeIndex = 0,
    anyCommandsAtAll = false,
    onChoose,
    onHighlight
  }: {
    /** The commands that matched what has been typed, in the order they are offered. */
    commands: readonly AgentCommand[];
    activeIndex?: number;
    /** Whether there are any commands here to filter. What tells a filter that matched
     *  none apart from there being nothing to match against. */
    anyCommandsAtAll?: boolean;
    onChoose: (command: AgentCommand) => void;
    onHighlight: (index: number) => void;
  } = $props();

  let menuElement = $state<HTMLDivElement | null>(null);

  // The list is capped and scrolls, so the highlight brings itself into view: an arrow key
  // that moved something nobody can see would look like a key that did nothing.
  $effect(() => {
    activeIndex;
    menuElement
      ?.querySelector<HTMLElement>("[data-conversation-command-active]")
      ?.scrollIntoView({ block: "nearest" });
  });
</script>

<div
  class="chat-menu"
  bind:this={menuElement}
  data-conversation-commands
  role="menu"
  aria-label="Commands"
>
  {#if commands.length === 0}
    <div class="chat-menu-hd" data-conversation-commands-empty>
      {anyCommandsAtAll ? "No command matches that." : "No commands here."}
    </div>
  {:else}
    <!-- Drawn in the order they were offered in and keyed by nothing else. The list is
         the agent's own, so two of its commands can carry the same name — a project one
         shadowing a user one — and a name used as a key would be a duplicate key, which
         is a crash inside the composer rather than a menu with a repeat in it. -->
    {#each commands as command, index}
      <button
        type="button"
        class="chat-menu-item"
        class:on={index === activeIndex}
        role="menuitem"
        data-conversation-command={command.name}
        data-conversation-command-active={index === activeIndex ? "true" : undefined}
        onmouseenter={() => onHighlight(index)}
        onmousedown={(event) => event.preventDefault()}
        onclick={() => onChoose(command)}
      >
        <span class="chat-menu-name">/{command.name}</span>
        {#if command.argument_hint}
          <span class="c2-command-argument" data-conversation-command-argument
            >{command.argument_hint}</span
          >
        {/if}
        <span class="chat-menu-desc">{command.description}</span>
      </button>
    {/each}
  {/if}
</div>

<style>
  /* What the command takes after its name, where the backend said. In the command's own
     type and quieter than it, because it is a shape to fill in rather than a word. */
  .c2-command-argument {
    flex: none;
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
  }
</style>
