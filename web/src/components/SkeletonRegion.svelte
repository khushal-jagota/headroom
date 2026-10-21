<script lang="ts">
  import { onMount, untrack, type Snippet } from "svelte";
  import type { SkeletonShape, SkeletonLayout } from "../lib/skeleton/contracts";
  import { fadeFlowContent } from "../lib/skeleton/fadeFlowContent";
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
  let skeleton: HTMLElement | undefined = $state();
  let mounted = $state(false);
  let visible = $state(false);
  let announced = $state(false);
  let wait = $state(0);
  let timers: ReturnType<typeof setTimeout>[] = [];
  let leaving = $state(false);
  let arriving = $state(untrack(() => !loading));
  let from = $state("1");
  let height = $state(0);
  const active = $derived(loading || visible);
  const regionHeight = $derived(height || reservation[shape]);
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

  function finishSkeleton() {
    visible = false;
    leaving = false;
  }

  function cancelTimers() {
    timers.forEach(clearTimeout);
    timers = [];
  }

  $effect.pre(() => {
    const pending = loading;
    if (!mounted) return;
    untrack(() => {
      cancelTimers();
      announced = false;
      if (pending) {
        const currentWait = ++wait;
        visible = false;
        leaving = false;
        arriving = false;
        // The mounted region owns the effective token, including inherited overrides.
        const token = getComputedStyle(region).getPropertyValue("--skeleton-delay").trim();
        const delay = Math.max(0, parseFloat(token) || 0) * (token.endsWith("ms") ? 1 : 1000);
        timers = [
          setTimeout(() => { if (wait === currentWait && loading) announced = true; }, delay * announcementDelayRatio),
          setTimeout(() => { if (wait === currentWait && loading) visible = true; }, delay)
        ];
      } else {
        arriving = !matchMedia("(prefers-reduced-motion: reduce)").matches;
        if (!arriving) finishSkeleton();
        else if (visible) {
          // Read the interrupted entrance before removing its animation.
          from = skeleton ? getComputedStyle(skeleton).opacity : "0";
          leaving = true;
        }
      }
    });
  });

  onMount(() => {
    mounted = true;
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotion = () => {
      if (media.matches && !loading) {
        finishSkeleton();
        arriving = false;
      }
    };
    updateMotion();
    media.addEventListener("change", updateMotion);
    const observer = new ResizeObserver(([entry]) => { height = entry.contentRect.height; });
    observer.observe(region);
    return () => {
      cancelTimers();
      ++wait;
      observer.disconnect();
      media.removeEventListener("change", updateMotion);
      region.getAnimations({ subtree: true }).forEach(animation => animation.cancel());
    };
  });
</script>

<!-- Keep the live announcement outside the busy subtree. Its stable node also
     announces later waits after an idle interval. -->
<span class="skeleton-status" role="status">{loading && announced ? loadingText : ""}</span>
<svelte:element this={shape === "inline" ? "span" : "div"} bind:this={region} class="resource skeleton-region" class:skeleton-region--fill={layout === "fill"}
  class:skeleton-region--inline={shape === "inline"} data-active={active || undefined}
  data-shape={shape} aria-busy={loading} style:--skeleton-reservation={`${reservation[shape]}px`}>
  {#key wait}
  {#if visible}
    <svelte:element this={shape === "inline" ? "span" : "div"} bind:this={skeleton} class="resource-state skeleton" class:skeleton--up={shape === "conversation"}
      class:skeleton--inline={shape === "inline"} aria-hidden="true"
      data-entering={loading || undefined} data-leaving={leaving || undefined}
      style:--skeleton-from={from}
      onanimationend={(event: AnimationEvent) => { if (event.target === skeleton && event.animationName === "skeleton-out" && leaving && !loading) finishSkeleton(); }}>
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
    <!-- The entrance remains a lifecycle controller when this wrapper is
         display:contents. Its animationend clears arriving and stops the flow
         action's subtree observer. Do not remove it because the wrapper paints
         nothing, or replace it with a transition that emits no animationend. -->
    <svelte:element this={shape === "inline" ? "span" : "div"} class="skeleton-content" class:skeleton-content--inline={shape === "inline"} class:resource-state={layout === "fill"} class:resource-arriving={arriving}
      use:fadeFlowContent={layout === "flow" && shape !== "inline" && arriving}
      onanimationend={(event: AnimationEvent) => { if (event.target === event.currentTarget && event.animationName === "skeleton-in" && !loading) arriving = false; }}>
      {@render children?.()}
    </svelte:element>
  {/if}
  {/key}
</svelte:element>
