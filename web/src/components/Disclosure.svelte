<script lang="ts">
  /** A fold whose openness the reader owns.
   *
   * `defaultOpen` is a default and not a setting: it says how this fold opens before
   * anybody has touched it, which is how a Ticket's current stage arrives open and a
   * Sprint's unwritten document does not. A refresh recomputes that value, so a fold that
   * kept reading it would shut under the reader the moment the data underneath moved on.
   * After the first toggle the reader owns this fold and later values are ignored.
   *
   * That is enough wherever the fold outlives the change. Where a refresh can move the
   * fold to another part of the page, this component dies with it, so an owner that
   * survives the move remembers the answer through `onReaderToggle` and hands it back as
   * `defaultOpen`.
   */
  import type { Snippet } from "svelte";

  let {
    variant = "content",
    defaultOpen = false,
    class: extraClass = "",
    title,
    summary,
    chevron = "trailing",
    onReaderToggle,
    children,
    ...rest
  }: {
    variant?: string;
    defaultOpen?: boolean;
    class?: string;
    title?: string;
    summary?: Snippet;
    chevron?: "trailing" | "leading" | "none";
    /** Called when the reader opens or closes this fold, never when the data does. */
    onReaderToggle?: (open: boolean) => void;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();

  let element = $state<HTMLDetailsElement | null>(null);
  /** How the fold is drawn before it exists, so an already-open fold never paints shut
   *  for a frame. Read once: the attribute must not be rewritten when the data moves. */
  // svelte-ignore state_referenced_locally
  const openAtFirstPaint = defaultOpen;
  /** How this fold stood when the data last set it, which is what tells a reader's
   *  toggle apart from this component's own write. */
  // svelte-ignore state_referenced_locally
  let setFromData = defaultOpen;
  /** Deliberately not `$state`: nothing is drawn from it, and the effect below must not
   *  run again because it changed. */
  let readerOwnsIt = false;

  $effect(() => {
    const wanted = defaultOpen;
    if (readerOwnsIt || !element) return;
    setFromData = wanted;
    element.open = wanted;
  });

  function toggled(): void {
    if (!element) return;
    if (!readerOwnsIt) {
      // Writing `element.open` above raises this event too, so the first touch counts
      // only when the fold disagrees with what the data last set.
      if (element.open === setFromData) return;
      readerOwnsIt = true;
    }
    // Once the reader owns the fold nothing else writes it, so every later toggle is
    // theirs, including one that puts it back where the data had it.
    onReaderToggle?.(element.open);
  }
</script>

<details
  bind:this={element}
  class={`disclosure disclosure--${variant}${extraClass ? ` ${extraClass}` : ""}`}
  open={openAtFirstPaint}
  ontoggle={toggled}
  {...rest}
>
  <summary class="disclosure-summary">
    {#if chevron === "leading"}
      <span class="disclosure-chev" aria-hidden="true"></span>
    {/if}
    {#if summary}
      {@render summary()}
    {:else}
      <span class="disclosure-title">{title}</span>
    {/if}
    {#if chevron === "trailing"}
      <span class="disclosure-chev" aria-hidden="true"></span>
    {/if}
  </summary>
  <div class="disclosure-body">
    {@render children?.()}
  </div>
</details>
