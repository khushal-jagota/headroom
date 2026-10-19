<script lang="ts">
  /* The recap's place, which changes size when the recap does.
   *
   * Asking for a recap clears the line, so this space shrinks to whatever an empty recap
   * shows and grows again when the new one lands. Both happen while someone is looking
   * straight at it, so it moves rather than jumps.
   */
  import type { Snippet } from "svelte";

  let { children }: { children: Snippet } = $props();

  let frame = $state<HTMLDivElement | null>(null);
  let content = $state<HTMLDivElement | null>(null);

  $effect(() => {
    const outer = frame;
    const inner = content;
    if (!outer || !inner) return;
    let last = inner.offsetHeight;
    let settle: ReturnType<typeof setTimeout> | undefined;
    const rest = () => {
      outer.style.height = "";
      outer.style.overflow = "";
    };
    const observer = new ResizeObserver(() => {
      const next = inner.offsetHeight;
      if (next === last) return;
      clearTimeout(settle);
      outer.style.overflow = "hidden";
      outer.style.height = `${last}px`;
      void outer.offsetHeight; // take the old height before asking for the new one
      outer.style.height = `${next}px`;
      last = next;
      settle = setTimeout(rest, 260);
    });
    observer.observe(inner);
    return () => {
      observer.disconnect();
      clearTimeout(settle);
      rest();
    };
  });
</script>

<div bind:this={frame} class="recap-slot" data-recap-slot>
  <div bind:this={content}>{@render children()}</div>
</div>

<style>
  .recap-slot { transition:height var(--motion-base) var(--motion-ease); }
  @media (prefers-reduced-motion:reduce) { .recap-slot { transition:none; } }
</style>
