<script lang="ts">
  import { onDestroy } from "svelte";
  import { fetchJson } from "../lib/api";
  import { mutateJson, resource } from "../lib/resources";
  import type { IdeasResponse } from "../lib/types";
  import Chip from "../components/Chip.svelte";
  import ErrorLine from "../components/ErrorLine.svelte";
  import MarkdownBlock from "../components/MarkdownBlock.svelte";
  import SegmentedControl from "../components/SegmentedControl.svelte";

  const ideas = resource<IdeasResponse>("ideas", (signal) =>
    fetchJson("/api/ideas", { signal })
  );

  const projectOptions = [
    { value: null, label: "None" },
    { value: "Vylo", label: "Vylo" },
    { value: "Tribe", label: "Tribe" },
    { value: "Learning", label: "Learning" },
    { value: "Other", label: "Other" }
  ];
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  let title = $state("");
  let detail = $state("");
  let project = $state<string | null>(null);
  let createError = $state<unknown>(null);
  let creating = $state(false);

  function relDate(seconds: unknown): string {
    const secs = Number(seconds);
    if (!Number.isFinite(secs)) return "";
    const days = Math.floor((Date.now() / 1000 - secs) / 86400);
    if (days <= 0) return "today";
    if (days < 7) return `${days}d`;
    const date = new Date(secs * 1000);
    return `${months[date.getMonth()]} ${date.getDate()}`;
  }

  async function capture(): Promise<void> {
    if (!title.trim()) return;
    creating = true;
    createError = null;
    const payload: Record<string, unknown> = { title: title.trim() };
    if (detail.trim()) payload.body = detail;
    if (project) payload.project = project;
    try {
      await mutateJson("/api/ideas", { method: "POST", body: payload }, ["ideas"]);
    } catch (err) {
      createError = err;
      creating = false;
    }
  }

  function keydown(event: KeyboardEvent): void {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void capture();
    }
  }

  onDestroy(() => ideas.dispose());
</script>

<section class="ideas-screen" data-screen="ideas">
  <div class="doc">
    <header class="head">
      <div class="row">
        <div class="title">Ideas</div>
        <div class="meta"><span class="pill">{ideas.data?.ideas?.length || 0}</span></div>
      </div>
    </header>
    <div class="col">
      <section class="capture" data-create="idea">
        {#if createError}<ErrorLine error={createError} />{/if}
        <input
          class="in title-in"
          type="text"
          placeholder="Capture an idea..."
          data-input="title"
          bind:value={title}
          onkeydown={keydown}
        />
        <textarea
          class="in detail-in"
          rows="2"
          placeholder="Add detail — optional"
          data-input="body"
          bind:value={detail}
        ></textarea>
        <div class="foot">
          <SegmentedControl name="project" options={projectOptions} bind:value={project} />
          <div class="spacer"></div>
          <span class="hint"><kbd>↩</kbd> to capture</span>
          <button
            class="commit"
            type="button"
            data-commit
            disabled={creating || !title.trim()}
            onclick={() => void capture()}
          >
            Capture
          </button>
        </div>
      </section>
      <div class="list" data-ideas>
        {#if ideas.error}
          <ErrorLine error={ideas.error} />
        {:else if ideas.loading && !ideas.data}
          <div class="quiet-line">Loading ideas...</div>
        {:else if !(ideas.data?.ideas || []).length}
          <div class="quiet-line">No ideas yet.</div>
        {:else}
          <div class="glabel">Captured</div>
          {#each ideas.data?.ideas || [] as idea}
            {@const hasBody = idea.body !== null && idea.body !== undefined && String(idea.body).trim() !== ""}
            {#if hasBody}
              <details class="idea" data-idea-id={idea.id}>
                <summary>
                  <span class="chev">›</span>
                  <span class="it entity-row-title">{idea.title}</span>
                  {#if idea.project}<Chip variant="project" value={idea.project} />{/if}
                  <span class="when">{relDate(idea.created_at)}</span>
                </summary>
                <div class="body"><MarkdownBlock text={idea.body} /></div>
              </details>
            {:else}
              <div class="flat" data-idea-id={idea.id}>
                <span class="chev"></span>
                <span class="it entity-row-title">{idea.title}</span>
                {#if idea.project}<Chip variant="project" value={idea.project} />{/if}
                <span class="when">{relDate(idea.created_at)}</span>
              </div>
            {/if}
          {/each}
        {/if}
      </div>
    </div>
  </div>
</section>
