<script lang="ts">
  let { text = "", quiet = "(none)" }: { text?: unknown; quiet?: string } = $props();
  let host: HTMLDivElement;

  function renderMarkdown(value: unknown): void {
    if (!host) return;
    const raw = value === null || value === undefined ? "" : String(value);
    host.replaceChildren();
    if (!raw.trim()) {
      const empty = document.createElement("div");
      empty.className = "quiet-line";
      empty.textContent = quiet;
      host.appendChild(empty);
      return;
    }
    const rendered = window.Planner?.markdown?.render(raw);
    if (rendered) {
      rendered.classList.add("markdown-block");
      host.appendChild(rendered);
      return;
    }
    const fallback = document.createElement("div");
    fallback.className = "markdown markdown-block";
    fallback.textContent = raw;
    host.appendChild(fallback);
  }

  $effect(() => {
    renderMarkdown(text);
  });
</script>

<div class="markdown-host" bind:this={host}></div>

<style>
  .markdown-host {
    display: contents;
  }
</style>
