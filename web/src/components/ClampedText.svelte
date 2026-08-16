<script lang="ts">
  /** A block of prose clamped to its first few lines, with a way to see the rest.
   *
   * Three screens show a long piece of writing at the head of a document — the Ticket's
   * recap, the Sprint Item's brief, the Sprint's primary bet — and all three want the same
   * thing: a few lines at rest, everything on demand, and the offer to see the rest only
   * when there is something being hidden. Whether there is can only be answered by the
   * browser, so it is measured here, once, rather than copied into each screen.
   *
   * The clamp is a max-height in CSS and the measurement is the same number of lines in
   * script, so `lines` sets both: it is written onto the element as `--clamp-lines` and
   * used as the yardstick the content's real height is compared against.
   *
   * The clamp releases while anything inside has focus, so editing always shows the whole
   * text. That is CSS (.ticket-recap--clamped:focus-within) and needs nothing from here.
   *
   * The element wears the `.ticket-recap` trio, which is where this voice was written and
   * is now what all three screens draw.
   */
  import type { Snippet } from "svelte";
  import type { HTMLAttributes } from "svelte/elements";

  interface Props extends HTMLAttributes<HTMLElement> {
    /** The prose itself. */
    children: Snippet;
    /** How many lines the clamp leaves visible, and so what counts as overflowing. */
    lines?: number;
    /** Changes when the prose changes, which is when the measurement is taken again. */
    contentKey?: unknown;
    /** Classes the host adds to the block. */
    class?: string;
    /** Whether the whole text is showing. Bindable: a host may reset it. */
    expanded?: boolean;
    /** Whether there is anything hidden. Bindable: a host may make the block the control. */
    canExpand?: boolean;
    /** Keep the clamp even when the text fits inside it. */
    clampEvenWhenItFits?: boolean;
    /** Render the "Show more" control under the block. */
    moreControl?: boolean;
    /** What the host wants on that control besides its class. */
    moreControlAttributes?: Record<string, string | boolean>;
  }

  let {
    children,
    lines = 3,
    contentKey = undefined,
    class: hostClass = "",
    expanded = $bindable(false),
    canExpand = $bindable(false),
    clampEvenWhenItFits = false,
    moreControl = true,
    moreControlAttributes = {},
    ...rest
  }: Props = $props();

  let element = $state<HTMLElement | null>(null);
  let clamped = $derived(!expanded && (canExpand || clampEvenWhenItFits));

  $effect(() => {
    const node = element;
    void contentKey;
    void lines;
    if (!node) return;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(node).lineHeight);
      const maxHeight = Number.isFinite(lineHeight) ? lineHeight * lines : node.clientHeight;
      canExpand = node.scrollHeight > maxHeight + 1;
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    const frame = requestAnimationFrame(measure);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  });
</script>

<div
  bind:this={element}
  class={hostClass ? `ticket-recap ${hostClass}` : "ticket-recap"}
  class:ticket-recap--clamped={clamped}
  style:--clamp-lines={lines}
  {...rest}
>{@render children()}</div>
{#if moreControl && canExpand}
  <button
    type="button"
    class="ticket-recap-more"
    {...moreControlAttributes}
    onclick={() => (expanded = !expanded)}
  >{expanded ? "Show less" : "Show more"}</button>
{/if}
