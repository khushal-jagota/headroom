<script lang="ts">
  import { settingsSearch, openSettingsSearchHit } from "../../lib/settings/search.svelte";

  /* Search results as rows: the label with the matched words marked, then the section path
     and the current value. The highlighted row follows the arrow keys in the field. */
  const LIMIT = 40;
  let words = $derived(settingsSearch.query.trim().split(/\s+/).filter(Boolean).map(word => word.toLowerCase()));

  function parts(text: string): { text: string; marked: boolean }[] {
    const lower = text.toLowerCase();
    const marks = new Array<boolean>(text.length).fill(false);
    for (const word of words) {
      for (let at = lower.indexOf(word); at >= 0; at = lower.indexOf(word, at + word.length)) marks.fill(true, at, at + word.length);
    }
    const out: { text: string; marked: boolean }[] = [];
    for (let index = 0; index < text.length; index += 1) {
      const last = out[out.length - 1];
      if (last && last.marked === marks[index]) last.text += text[index];
      else out.push({ text: text[index], marked: marks[index] });
    }
    return out;
  }
</script>

{#if settingsSearch.hits.length === 0}
  <p class="settings-search-empty">No settings match “{settingsSearch.query.trim()}”.</p>
{:else}
  <div class="settings-search-results" role="listbox" aria-label="Search results" data-settings-search-results>
    {#each settingsSearch.hits.slice(0, LIMIT) as hit, index (`${hit.href}|${hit.label}|${hit.where}`)}
      <a class="settings-search-hit" class:settings-search-hit--highlighted={index === settingsSearch.highlighted} href={hit.href} role="option" aria-selected={index === settingsSearch.highlighted} tabindex="-1"
        onclick={(event) => { event.preventDefault(); openSettingsSearchHit(hit); }}
        onpointerenter={() => (settingsSearch.highlighted = index)}>
        <span class="settings-search-label">
          <span class="settings-search-title">{#each parts(hit.label) as part}{#if part.marked}<mark>{part.text}</mark>{:else}{part.text}{/if}{/each}</span>
          {#if hit.where}<span class="settings-search-where">{hit.where}</span>{/if}
        </span>
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" /></svg>
      </a>
    {/each}
  </div>
{/if}

<style>
  .settings-search-empty { margin-top:var(--page-block-gap); color:var(--tone-tertiary); font-size:var(--type-body); }
  .settings-search-results { margin-top:var(--page-block-gap); border-top:var(--border-hairline) solid var(--border-color); }
  .settings-search-hit { position:relative; display:flex; align-items:center; gap:var(--space-4); min-height:52px; padding:var(--space-3) 0; color:inherit; text-decoration:none; cursor:pointer; }
  .settings-search-hit + .settings-search-hit { border-top:var(--border-hairline) solid var(--border-color); }
  .settings-search-hit::before { content:""; position:absolute; inset:2px calc(-1 * var(--page-row-outset)); border-radius:var(--radius-md); background:transparent; transition:background var(--motion-fast) var(--motion-ease); pointer-events:none; }
  .settings-search-hit--highlighted::before { background:var(--wash-context); }
  .settings-search-label { flex:1; min-width:0; display:block; }
  .settings-search-title { display:block; color:var(--tone-primary); font-size:var(--type-body); }
  .settings-search-where { display:block; margin-top:2px; font-size:var(--type-small); line-height:1.45; color:var(--tone-tertiary); }
  .settings-search-hit svg { flex:none; width:14px; height:14px; color:var(--tone-faint); }
  .settings-search-hit--highlighted svg { color:var(--tone-secondary); }
  mark { background:color-mix(in srgb, var(--accent-bright) 22%, transparent); color:inherit; border-radius:2px; }
  @media (max-width: 720px) { .settings-search-hit::before { inset:2px 0; } }
</style>
