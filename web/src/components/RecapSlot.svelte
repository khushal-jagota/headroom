<script lang="ts">
  /* The recap's place, which changes size when the recap does.
   *
   * A hover panel can hold the largest height of one pointer visit. Empty or shorter
   * recaps then leave space until the pointer leaves. Other callers resize normally.
   */
  import type { Snippet } from "svelte";

  let { children, holdHeight = false }: { children: Snippet; holdHeight?: boolean } = $props();

  let frame = $state<HTMLDivElement | null>(null);
  let content = $state<HTMLDivElement | null>(null);
  let setHoldHeight = $state<((hold: boolean) => void) | null>(null);

  $effect(() => {
    const outer = frame;
    const inner = content;
    if (!outer || !inner) return;
    let last = inner.offsetHeight;
    let holding = false;
    let floor = 0;
    let settle: ReturnType<typeof setTimeout> | undefined;
    const rest = () => {
      outer.style.height = holding ? `${Math.max(floor, inner.offsetHeight)}px` : "";
      outer.style.overflow = "";
    };
    const resize = () => {
      const rendered = outer.getBoundingClientRect().height;
      const contentHeight = inner.offsetHeight;
      if (holding) floor = Math.max(floor, rendered, contentHeight);
      const next = holding ? floor : contentHeight;
      if (next === last) {
        // Pin even an unchanged target before content can disappear underneath it.
        if (holding) outer.style.height = `${next}px`;
        else if (!settle) rest();
        return;
      }
      clearTimeout(settle);
      // An auto-sized frame already follows new content before this callback.
      // Keep its previous content height as the ordinary animation's starting point.
      const from = holding || outer.style.height !== "" ? rendered : last;
      outer.style.overflow = "hidden";
      outer.style.height = `${from}px`;
      void outer.offsetHeight; // take the old height before asking for the new one
      outer.style.height = `${next}px`;
      last = next;
      settle = setTimeout(() => { settle = undefined; rest(); }, 260);
    };
    // Hold changes do not replace the observer or run its cleanup. In particular,
    // entering during a shrink must retain the visible frame, not its smaller target.
    setHoldHeight = (hold) => {
      if (holding === hold) return;
      holding = hold;
      floor = 0;
      resize();
    };
    const observer = new ResizeObserver(resize);
    observer.observe(inner);
    return () => {
      observer.disconnect();
      clearTimeout(settle);
      setHoldHeight = null;
      outer.style.height = "";
      outer.style.overflow = "";
    };
  });
  $effect(() => { setHoldHeight?.(holdHeight); });
</script>

<div bind:this={frame} class="recap-slot" data-recap-slot>
  <div bind:this={content}>{@render children()}</div>
</div>

<style>
  .recap-slot { transition:height var(--motion-base) var(--motion-ease); }
  @media (prefers-reduced-motion:reduce) { .recap-slot { transition:none; } }
</style>
