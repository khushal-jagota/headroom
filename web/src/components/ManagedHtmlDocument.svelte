<script lang="ts">
  import { onDestroy, tick, untrack } from "svelte";
  import type { RegisterManagedArtifactFrame } from "../lib/managedArtifactPress";
  import SkeletonRegion from "./SkeletonRegion.svelte";
  import { MANAGED_HTML_PREVIEW_SANDBOX, prepareManagedHtmlPreviewDocument } from "../lib/filePreview";
  import { readManagedFile } from "../lib/managedFileRead";
  import { resolveManagedHtmlNavigation } from "../lib/managedHtmlNavigation";

  let { href, label, active = true, reloadSignal = 0, full = false, registerArtifactFrame }: {
    href: string; label: string; active?: boolean; reloadSignal?: number; full?: boolean; registerArtifactFrame?: RegisterManagedArtifactFrame;
  } = $props();
  let host: HTMLDivElement;
  let frame = $state<HTMLIFrameElement>();
  let frameHref = $state<string | null>(null);
  let notice = $state("");
  let fetchFailed = $state(false);
  let history = $state<string[]>([]);
  let currentHref = "";
  let rootHref = "";
  let generation = "";
  let request = 0;
  let controller: AbortController | null = null;
  let objectUrl: string | null = null;

  function visible(): boolean {
    if (!active || !host?.isConnected || document.visibilityState === "hidden") return false;
    if (host.closest('[data-visible-content-state="retained"], [data-visible-content-state="released"]')) return false;
    if (!frame?.isConnected) return false;
    const rect = frame.getBoundingClientRect();
    if (!frame.getClientRects().length || getComputedStyle(frame).visibility === "hidden") return false;
    let left = Math.max(0, rect.left), top = Math.max(0, rect.top);
    let right = Math.min(window.innerWidth, rect.right), bottom = Math.min(window.innerHeight, rect.bottom);
    for (let ancestor = frame.parentElement; ancestor; ancestor = ancestor.parentElement) {
      const style = getComputedStyle(ancestor);
      const bounds = ancestor.getBoundingClientRect();
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowX)) {
        left = Math.max(left, bounds.left + ancestor.clientLeft);
        right = Math.min(right, bounds.left + ancestor.clientLeft + ancestor.clientWidth);
      }
      if (/^(auto|scroll|hidden|clip)$/.test(style.overflowY)) {
        top = Math.max(top, bounds.top + ancestor.clientTop);
        bottom = Math.min(bottom, bounds.top + ancestor.clientTop + ancestor.clientHeight);
      }
    }
    return right > left && bottom > top;
  }
  function cancel() { request++; controller?.abort(); controller = null; }
  function release() {
    cancel();
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    frameHref = null;
    fetchFailed = false;
    generation = "";
  }
  function artifactFrame(element: HTMLIFrameElement) {
    return { destroy: registerArtifactFrame?.(element) };
  }
  function fragment(hash: string) {
    frame?.contentWindow?.postMessage({ type: "panels-html-fragment", generation, hash }, "*");
  }
  async function load(candidate: URL, nextHistory: string[], fresh = false, requireVisible = false) {
    cancel();
    const sequence = request;
    const abort = new AbortController();
    controller = abort;
    notice = "";
    fetchFailed = false;
    try {
      const body = await readManagedFile(candidate.pathname, abort.signal, {artifactRootHref: rootHref, fresh});
      if (abort.signal.aborted || sequence !== request || !active || (requireVisible && !visible())) return;
      const nextGeneration = crypto.randomUUID();
      const prepared = prepareManagedHtmlPreviewDocument(body, candidate.href, Boolean(registerArtifactFrame), nextGeneration);
      const previous = objectUrl;
      objectUrl = URL.createObjectURL(new Blob([prepared], {type: 'text/html'}));
      currentHref = candidate.pathname + candidate.hash;
      history = nextHistory;
      generation = nextGeneration;
      frameHref = objectUrl;
      if (previous) void tick().then(() => URL.revokeObjectURL(previous));
    } catch (error) {
      if (!abort.signal.aborted && sequence === request && (!requireVisible || visible())) {
        notice = error instanceof Error ? error.message : String(error);
        fetchFailed = true;
      }
    }
  }
  function navigate(raw: string, back = false) {
    const candidate = resolveManagedHtmlNavigation(raw, currentHref || rootHref, rootHref, window.location.origin);
    if (!candidate) { cancel(); notice = 'This link cannot be opened in the artifact viewer.'; return; }
    const nextHistory = back ? history.slice(0, -1) : [...history, currentHref];
    const current = new URL(currentHref, window.location.origin);
    if (candidate.pathname === current.pathname) {
      cancel();
      notice = '';
      history = nextHistory;
      currentHref = candidate.pathname + candidate.hash;
      fragment(candidate.hash);
    } else void load(candidate, nextHistory, false, true);
  }
  function receive(event: MessageEvent) {
    if (!visible() || !frame || event.source !== frame.contentWindow) return;
    if (event.data?.type !== 'panels-html-navigation' || event.data.generation !== generation || typeof event.data.href !== 'string') return;
    navigate(event.data.href);
  }
  $effect(() => {
    const initialHref = href;
    const enabled = active;
    const refresh = reloadSignal;
    untrack(() => {
      if (rootHref !== initialHref) {
        release(); rootHref = initialHref; currentHref = initialHref; history = []; notice = '';
      }
      if (!enabled) { release(); return; }
      const candidate = resolveManagedHtmlNavigation(currentHref, currentHref, rootHref, window.location.origin);
      if (candidate) void load(candidate, history, refresh > 0);
      else notice = 'This link cannot be opened in the artifact viewer.';
    });
    return cancel;
  });
  onDestroy(release);
</script>

<svelte:window onmessage={receive} />
<div class:full class="managed-html-document" bind:this={host} data-visible-content-failed={fetchFailed && !frameHref || undefined}>
  {#if history.length > 0 || notice}
    <div class="managed-html-controls">
      {#if history.length > 0}<button type="button" data-html-back onclick={() => navigate(history[history.length - 1], true)}>Back</button>{/if}
      {#if notice}<span class="quiet-line" role="status" data-html-navigation-notice>{notice}</span>{/if}
    </div>
  {/if}
  <SkeletonRegion shape="document" layout={full ? "fill" : "flow"} loading={!notice && !frameHref} loadingText="Loading preview...">
    {#if frameHref}
      <iframe use:artifactFrame bind:this={frame} class={full ? "file-preview-document-frame" : "file-preview-frame"} data-file-preview-html src={frameHref}
        sandbox={MANAGED_HTML_PREVIEW_SANDBOX} title={label} onload={() => fragment(new URL(currentHref, window.location.origin).hash)}></iframe>
    {/if}
  </SkeletonRegion>
</div>
<style>
  .managed-html-document { min-height: 0; width: 100%; }
  .managed-html-document.full { height: 100%; display: flex; flex-direction: column; }
  .managed-html-controls { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2); flex: none; }
</style>
