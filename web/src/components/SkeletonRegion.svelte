<script lang="ts">
  import { onMount, untrack, type Snippet } from "svelte";
  import type { SkeletonShape, SkeletonLayout } from "../lib/skeleton/contracts";
  import { rowWidths, rowHeight, documentBlockHeight, documentBlockStep, documentHeadHeight,
    conversationPattern, conversationGap, reservation } from "../lib/skeleton/config";

  let { shape, loading = true, loadingText, layout = "flow", children }: {
    shape: SkeletonShape;
    loading?: boolean;
    loadingText: string;
    layout?: SkeletonLayout;
    children?: Snippet;
  } = $props();
  let region: HTMLElement;
  let skeleton: HTMLElement | undefined = $state();
  let visible = $state(untrack(() => loading));
  let leaving = $state(false);
  let arriving = $state(false);
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

  function finish() {
    visible = false;
    leaving = false;
    arriving = false;
  }

  $effect.pre(() => {
    const pending = loading;
    untrack(() => {
      if (pending) {
        visible = true;
        leaving = false;
        arriving = false;
      } else if (visible) {
        if (matchMedia("(prefers-reduced-motion: reduce)").matches || shape === "inline") finish();
        else {
          // Read the interrupted entrance before removing its animation.
          from = skeleton ? getComputedStyle(skeleton).opacity : "0";
          leaving = true;
          arriving = true;
        }
      }
    });
  });

  onMount(() => {
    const media = matchMedia("(prefers-reduced-motion: reduce)");
    const updateMotion = () => {
      if (media.matches && !loading) finish();
    };
    updateMotion();
    media.addEventListener("change", updateMotion);
    const observer = new ResizeObserver(([entry]) => { height = entry.contentRect.height; });
    observer.observe(region);
    return () => {
      observer.disconnect();
      media.removeEventListener("change", updateMotion);
      region.getAnimations({ subtree: true }).forEach(animation => animation.cancel());
    };
  });
</script>

<!-- Keep the live announcement outside the busy subtree. Its stable node also
     announces later waits after an idle interval. -->
<span class="skeleton-status" role="status">{loading ? loadingText : ""}</span>
<svelte:element this={shape === "inline" ? "span" : "div"} bind:this={region} class="resource skeleton-region" class:skeleton-region--fill={layout === "fill"}
  class:skeleton-region--inline={shape === "inline"} data-active={active || undefined}
  data-shape={shape} aria-busy={loading} style:--skeleton-reservation={`${reservation[shape]}px`}>
  {#if visible}
    <svelte:element this={shape === "inline" ? "span" : "div"} bind:this={skeleton} class="resource-state skeleton" class:skeleton--up={shape === "conversation"}
      class:skeleton--inline={shape === "inline"} aria-hidden="true"
      data-entering={loading && shape !== "inline" || undefined} data-leaving={leaving || undefined}
      style:--skeleton-from={from}
      onanimationend={(event: AnimationEvent) => { if (event.target === skeleton && event.animationName === "skeleton-out" && !loading) finish(); }}>
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
    <svelte:element this={shape === "inline" ? "span" : "div"} class="skeleton-content" class:resource-state={layout === "fill"} class:resource-arriving={arriving}>
      {@render children?.()}
    </svelte:element>
  {/if}
</svelte:element>
