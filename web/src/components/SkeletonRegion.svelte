<script lang="ts">
  import { onMount, untrack, type Snippet } from "svelte";
  import type { SkeletonShape, SkeletonLayout } from "../lib/skeleton/contracts";
  import { rowWidths, rowHeight, documentBlockHeight, documentBlockStep, documentHeadHeight,
    conversationPattern, conversationGap, reservation, announcementDelayRatio } from "../lib/skeleton/config";

  let { shape, loading = true, loadingText, layout = "flow", children }: {
    shape: SkeletonShape;
    loading?: boolean;
    loadingText: string;
    layout?: SkeletonLayout;
    children?: Snippet;
  } = $props();
  let region: HTMLElement;
  let mounted = $state(false);
  let visible = $state(false);
  let announced = $state(false);
  let fading = $state(false);
  let wait = $state(0);
  let timers: ReturnType<typeof setTimeout>[] = [];
  let height = $state(0);
  // The frame shape reserves its box in CSS, so it has no reservation to read here.
  const reserved = $derived(shape === "frame" ? null : reservation[shape]);
  const regionHeight = $derived(height || reserved || 0);
  const rows = $derived(Math.ceil((regionHeight + rowHeight - 39) / rowHeight));
  const blocks = $derived(Math.max(1, Math.ceil((regionHeight + 60 - documentHeadHeight) / documentBlockStep)));
  const turns = $derived.by(() => {
    const result: typeof conversationPattern = [];
    let drawn = 0;
    while (drawn < regionHeight + 80) {
      const turn = conversationPattern[result.length % conversationPattern.length];
      result.push(turn);
      drawn += turn.height + conversationGap;
    }
    return result.reverse();
  });

  function cancelTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  // The mounted region owns the effective token, including inherited overrides.
  function duration(name: string): number {
    const token = getComputedStyle(region).getPropertyValue(name).trim();
    return Math.max(0, parseFloat(token) || 0) * (token.endsWith("ms") ? 1 : 1000);
  }

  $effect.pre(() => {
    const pending = loading;
    if (!mounted) return;
    untrack(() => {
      cancelTimers();
      announced = false;
      if (pending) {
        fading = false;
        visible = false;
        const currentWait = ++wait;
        const delay = duration("--skeleton-delay");
        timers = [
          setTimeout(() => { if (wait === currentWait && loading) announced = true; }, delay * announcementDelayRatio),
          setTimeout(() => { if (wait === currentWait && loading) visible = true; }, delay)
        ];
      } else if (shape === "frame" && visible) {
        // Both layers exist for one crossfade, so the drawn skeleton leaves over the
        // revealed frame rather than at the instant it arrives.
        fading = true;
        const currentWait = wait;
        timers = [setTimeout(() => {
          if (wait !== currentWait || loading) return;
          visible = false;
          fading = false;
        }, duration("--skeleton-fade"))];
      } else {
        visible = false;
        fading = false;
      }
    });
  });

  onMount(() => {
    mounted = true;
    const observer = new ResizeObserver(([entry]) => { height = entry.contentRect.height; });
    observer.observe(region);
    return () => {
      cancelTimers();
      ++wait;
      observer.disconnect();
    };
  });
</script>

<!-- Keep the live announcement outside the busy subtree. Its stable node also
     announces later waits after an idle interval. -->
<span class="skeleton-status" role="status">{loading && announced ? loadingText : ""}</span>
<svelte:element this={shape === "inline" ? "span" : "div"} bind:this={region} class="resource skeleton-region" class:skeleton-region--fill={layout === "fill"}
  class:skeleton-region--inline={shape === "inline"} data-active={loading || undefined}
  data-shape={shape} aria-busy={loading} style:--skeleton-reservation={reserved === null ? undefined : `${reserved}px`}>
  {#if shape === "frame"}
  <!-- The frame layer stays mounted through the wait, so the artifact can draw behind the
       skeleton and report that it painted. -->
  <div class="skeleton-content resource-state">{@render children?.()}</div>
  {#if visible}
    <div class="resource-state skeleton skeleton--frame" class:skeleton--leaving={fading} aria-hidden="true">
      <div class="skeleton-frame">
        <div class="skeleton-document-eyebrow"><span class="skeleton-row skeleton-row--eyebrow"><span class="skeleton-line skeleton-line--xs" style="width:130px"></span></span></div>
        <div class="skeleton-document-title"><span class="skeleton-row skeleton-row--title"><span class="skeleton-line skeleton-line--title" style="width:74%"></span></span></div>
        <div class="skeleton-document-meta"><span class="skeleton-row skeleton-row--ui"><span class="skeleton-line" style="width:210px"></span></span></div>
        <div class="skeleton-block skeleton-frame-block"></div>
      </div>
    </div>
  {/if}
  {:else}
  {#key wait}
  {#if loading && visible}
    <svelte:element this={shape === "inline" ? "span" : "div"} class="resource-state skeleton" class:skeleton--up={shape === "conversation"}
      class:skeleton--inline={shape === "inline"} aria-hidden="true"
      data-entering="true">
      {#if shape === "rows"}
        <div class="skeleton-rows"><div class="skeleton-rows-head"></div>
        <div class="skeleton-rows-body">
          {#each Array(rows) as _, index}
            <div class="skeleton-list-row"><span class="skeleton-row"><span class="skeleton-line" style:width={rowWidths[index % rowWidths.length]}></span></span><span class="skeleton-row-mark"></span></div>
          {/each}
        </div>
        </div>
      {:else if shape === "document"}
        <div class="skeleton-document">
          <div class="skeleton-document-eyebrow"><span class="skeleton-row skeleton-row--eyebrow"><span class="skeleton-line skeleton-line--xs" style="width:130px"></span></span></div>
          <div class="skeleton-document-title"><span class="skeleton-row skeleton-row--title"><span class="skeleton-line skeleton-line--title" style="width:74%"></span></span></div>
          <div class="skeleton-document-meta"><span class="skeleton-row skeleton-row--ui"><span class="skeleton-line" style="width:210px"></span></span></div>
          {#each Array(blocks) as _}<div class="skeleton-block skeleton-document-block" style:height={`${documentBlockHeight}px`}></div>{/each}
        </div>
      {:else if shape === "conversation"}
        <div class="skeleton-thread">
          {#each turns as turn}<div class="skeleton-block" style:height={`${turn.height}px`} style:width={turn.user ? "46%" : "100%"} style:align-self={turn.user ? "flex-end" : "stretch"}></div>{/each}
        </div>
      {:else if shape === "inline"}
        <span class="skeleton-inline"></span>
      {:else}
        <div class="skeleton-block skeleton-whole-block"></div>
      {/if}
    </svelte:element>
  {/if}
  {#if !loading}
    <svelte:element this={shape === "inline" ? "span" : "div"} class="skeleton-content" class:skeleton-content--inline={shape === "inline"} class:resource-state={layout === "fill"}>
      {@render children?.()}
    </svelte:element>
  {/if}
  {/key}
  {/if}
</svelte:element>
