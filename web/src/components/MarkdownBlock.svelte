<script lang="ts">
  import type { RegisterMarkdownPresentation } from "../lib/markdown/presentationContracts";
  import { createMarkdownPresentation, type MarkdownPresentationController } from "../lib/markdown/textLocation";
  import { onDestroy, untrack } from "svelte";
  import { MARKDOWN_WORKER_THRESHOLD } from "../lib/markdown/config";
  import { observeVisibleContent, resetVisibleContentHeight } from "../lib/visibleContent";
  import {
    createManagedMarkdownSurface,
    type ReadOnlyManagedMarkdownSurface
  } from "../lib/managedMarkdown";

  let {
    text = "",
    quiet = "(none)",
    depth = 0,
    visited = [],
    registerPresentation
  }: {
    text?: unknown;
    quiet?: string;
    depth?: number;
    visited?: string[];
    registerPresentation?: RegisterMarkdownPresentation;
  } = $props();
  let host = $state<HTMLDivElement | null>(null);
  let surface: ReadOnlyManagedMarkdownSurface | null = null;
  let presentation = $state.raw<MarkdownPresentationController | null>(null);
  let active = $state(false);
  let pendingPresentation: { token: object; before: number } | null = null;
  function textHeight(target: HTMLElement): number {
    return target.querySelector('.markdown-block')?.getBoundingClientRect().height ?? 0;
  }
  function announce(target: HTMLElement, phase: 'before' | 'after', operation: {token:object;before:number}): void {
    untrack(() => target.dispatchEvent(new CustomEvent('conversation-text-presentation', {bubbles:true,
      detail:{...operation,phase,after:textHeight(target)}})));
  }
  const large = $derived(typeof text === "string" && text.length >= MARKDOWN_WORKER_THRESHOLD);

  const needsPresentation = $derived(registerPresentation !== undefined);

  // This facade exists before observer admission can create the managed surface.
  // Callback replacements change registry ownership, not the piece presentation.
  $effect(() => {
    if (!host || !needsPresentation) return;
    surface?.destroy(); surface = null;
    const controller = createMarkdownPresentation(host);
    presentation = controller;
    return () => {
      surface?.destroy(); surface = null;
      controller.destroy();
      if (presentation === controller) presentation = null;
    };
  });

  $effect(() => {
    const controller = presentation;
    const register = registerPresentation;
    const target = host;
    if (!target || !controller || !register) return;
    return untrack(() => register(target, controller.presentation));
  });

  $effect(() => {
    const source = text == null ? "" : String(text);
    const controller = presentation;
    untrack(() => controller?.updateSource(source));
    if (host) resetVisibleContentHeight(host);
  });

  $effect(() => {
    if (!host) return;
    if (!large) { active = true; return; }
    // A small document already owns a painted surface. Adopt that view into
    // the bounded policy without destroying it before a larger refresh parses.
    return observeVisibleContent(host, visible => {
      active = visible;
    }, { initiallyMounted: surface !== null });
  });

  $effect(() => {
    if (!host) return;
    if (!active) {
      surface?.destroy(); surface = null; host.replaceChildren(); host.removeAttribute("data-markdown-rendering"); return;
    }
    const target = host;
    target.setAttribute("data-markdown-rendering", "");
    if (target.closest('[data-conversation-row="streaming"], [data-conversation-row="agent_message"]')) {
      if (pendingPresentation) announce(target, 'after', pendingPresentation);
      pendingPresentation = {token:{}, before:textHeight(target)};
      announce(target, 'before', pendingPresentation);
    }
    surface ??= createManagedMarkdownSurface(target, { mode: "read-only", presentation: presentation ?? undefined, onRendered: () => {
      target.removeAttribute("data-markdown-rendering");
      if (pendingPresentation) announce(target, 'after', pendingPresentation);
      pendingPresentation = null;
    } });
    const input = { source: text, emptyText: quiet, depth, visited };
    untrack(() => surface?.update(input));
  });

  // Only in a browser. Nothing is mounted when this is rendered on a server, so there is
  // nothing to tear down — and asking to be told about a teardown that cannot happen is
  // what stopped this component being server-rendered at all.
  if (typeof window !== "undefined") {
    onDestroy(() => {
      surface?.destroy();
      surface = null;
    });
  }
</script>

<div class="markdown-host" class:markdown-host--large={large} bind:this={host}></div>

<style>
  .markdown-host--large { display: block !important; }
  .markdown-host {
    display: contents;
  }
</style>
