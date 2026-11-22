<script lang="ts">
  import { SETTINGS_GROUPS, settingsHref, type SettingsSection } from "../../lib/settings/address";
  import { returnPlace } from "../../lib/settings/returnPlace";
  import { settingsSearch, openSettingsSearchHit } from "../../lib/settings/search.svelte";
  import { useSettingsSearchEntries } from "../../lib/settings/searchEntries.svelte";
  import { searchSettings } from "../../lib/settings/searchIndex";
  import SettingsSectionIcon from "./SettingsSectionIcon.svelte";
  import SettingsSearchResults from "./SettingsSearchResults.svelte";

  /* Settings takes the workspace rail's place: Back to Panels, the search field, and the
     seven sections in three groups. On a phone this is the first screen. */
  let { section }: { section: SettingsSection } = $props();

  let field = $state<HTMLInputElement | null>(null);
  let searching = $derived(settingsSearch.query.trim() !== "");
  const entries = useSettingsSearchEntries(() => searching);
  $effect(() => {
    settingsSearch.hits = searching ? searchSettings(entries.current, settingsSearch.query) : [];
  });

  function fieldKey(event: KeyboardEvent): void {
    const count = settingsSearch.hits.length;
    if (event.key === "Escape") {
      event.preventDefault();
      settingsSearch.query = "";
      settingsSearch.highlighted = 0;
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && count > 0) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : count - 1;
      settingsSearch.highlighted = (settingsSearch.highlighted + step) % count;
    } else if (event.key === "Enter" && count > 0) {
      event.preventDefault();
      openSettingsSearchHit(settingsSearch.hits[Math.min(settingsSearch.highlighted, count - 1)]);
    }
  }

  function windowKey(event: KeyboardEvent): void {
    if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || event.defaultPrevented) return;
    const target = event.target as HTMLElement | null;
    if (target?.closest("input, textarea, select, [contenteditable='true'], [contenteditable='']")) return;
    event.preventDefault();
    field?.focus();
  }
</script>

<svelte:window onkeydown={windowKey} />

<nav class="settings-rail" aria-label="Settings" data-settings-rail>
  <div class="settings-rail-bubble">
    <h1 class="settings-rail-title">Settings</h1>
    <a class="settings-rail-back" href={returnPlace()} data-settings-back>
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5" /></svg>Back to Panels
    </a>
    <div class="settings-rail-search">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="7" cy="7" r="4.5" /><path d="m10.5 10.5 3 3" /></svg>
      <input bind:this={field} type="search" placeholder="Search settings" autocomplete="off" aria-label="Search settings" aria-keyshortcuts="/" bind:value={settingsSearch.query} oninput={() => (settingsSearch.highlighted = 0)} onkeydown={fieldKey} data-settings-search />
      <kbd aria-hidden="true">/</kbd>
    </div>
    {#if searching}
      <div class="settings-rail-results"><SettingsSearchResults /></div>
    {/if}
    <div class="settings-rail-sections" class:settings-rail-sections--searching={searching}>
      {#each SETTINGS_GROUPS as group (group.label)}
        <div class="settings-rail-group">{group.label}</div>
        {#each group.sections as item (item.key)}
          <a class="settings-rail-item" href={settingsHref(item.key)} aria-current={!searching && section === item.key ? "page" : undefined} data-settings-section={item.key}>
            <SettingsSectionIcon section={item.key} />{item.label}
            <svg class="settings-rail-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5" /></svg>
          </a>
        {/each}
      {/each}
    </div>
  </div>
</nav>

<style>
  .settings-rail { flex:1; min-height:0; display:flex; flex-direction:column; padding:var(--rail-bubble-gap); }
  .settings-rail-bubble { flex:1; min-height:0; display:flex; flex-direction:column; background:var(--rail-bubble-fill); border-radius:var(--rail-bubble-radius); padding:var(--space-2); overflow-y:auto; animation:settings-rail-in var(--motion-base) var(--motion-ease); }
  @keyframes settings-rail-in { from { opacity:0; transform:translateX(calc(-1 * var(--space-1))); } to { opacity:1; transform:none; } }
  .settings-rail-title { display:none; }
  .settings-rail-back { display:flex; align-items:center; gap:var(--space-2); height:var(--button-height); padding:0 var(--rail-pad); border-radius:var(--radius-md); color:var(--rail-tone-rest); font-size:var(--type-row); text-decoration:none; transition:background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .settings-rail-back svg { width:14px; height:14px; }
  .settings-rail-back:hover { background:var(--wash-hover); color:var(--rail-tone-bright); }
  .settings-rail-search { position:relative; margin:var(--space-2) 0 var(--space-3); }
  .settings-rail-search svg { position:absolute; left:var(--rail-pad); top:50%; width:14px; height:14px; margin-top:-7px; color:var(--tone-tertiary); pointer-events:none; }
  .settings-rail-search input { width:100%; height:var(--button-height); padding:0 var(--space-6) 0 calc(var(--rail-pad) + 20px); border:0; border-radius:var(--radius-md); background:var(--surface-input); color:var(--tone-primary); font:inherit; font-size:var(--type-row); }
  .settings-rail-search input::placeholder { color:var(--tone-faint); font-style:italic; }
  .settings-rail-search input::-webkit-search-cancel-button { display:none; }
  .settings-rail-search input:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:var(--focus-offset); }
  .settings-rail-search kbd { position:absolute; right:var(--space-2); top:50%; transform:translateY(-50%); padding:0 5px; border-radius:var(--radius-sm); background:var(--state-step); color:var(--tone-tertiary); font:var(--weight-small) var(--type-small)/18px var(--font-ui); }
  .settings-rail-search input:focus + kbd, .settings-rail-search input:not(:placeholder-shown) + kbd { display:none; }
  .settings-rail-results { display:none; }
  .settings-rail-group { margin:var(--space-4) 0 var(--space-1); padding:0 var(--rail-pad); font-size:var(--rail-heading-size); font-weight:var(--rail-heading-weight); letter-spacing:var(--rail-heading-tracking); line-height:var(--rail-heading-line); text-transform:uppercase; color:var(--rail-tone-heading); }
  .settings-rail-group:first-child { margin-top:var(--space-1); }
  .settings-rail-item { display:flex; align-items:center; gap:var(--space-2); width:100%; height:var(--button-height); padding:0 var(--rail-pad); border-radius:var(--radius-md); color:var(--rail-tone-rest); font-size:var(--type-row); text-decoration:none; transition:background var(--motion-fast) var(--motion-ease), color var(--motion-fast) var(--motion-ease); }
  .settings-rail-item :global(svg) { width:16px; height:16px; flex:none; }
  .settings-rail-item .settings-rail-chevron { display:none; }
  .settings-rail-item:hover { background:var(--wash-hover); color:var(--rail-tone-bright); }
  .settings-rail-item[aria-current="page"] { background:var(--wash-selected); color:var(--rail-tone-bright); }
  .settings-rail-back:focus-visible, .settings-rail-item:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:calc(-1 * var(--focus-width)); }
  @media (max-width: 720px) {
    .settings-rail { padding:0; }
    .settings-rail-bubble { background:none; border-radius:0; padding:var(--space-4) var(--page-gutter) var(--space-page-tail); animation:none; }
    .settings-rail-title { display:block; margin:var(--space-3) 0 var(--space-2); font-size:var(--type-page-title); font-weight:var(--weight-page-title); letter-spacing:var(--tracking-page-title); color:var(--tone-strong); }
    .settings-rail-back, .settings-rail-search kbd { display:none; }
    .settings-rail-search input { height:36px; }
    .settings-rail-results { display:block; }
    .settings-rail-sections--searching { display:none; }
    .settings-rail-group { margin-top:var(--space-5); padding:0; }
    .settings-rail-item { height:44px; padding:0; border-radius:0; color:var(--tone-primary); font-size:var(--type-body); }
    .settings-rail-item + .settings-rail-item { border-top:var(--border-hairline) solid var(--border-color); }
    .settings-rail-item .settings-rail-chevron { display:block; margin-left:auto; width:14px; height:14px; color:var(--tone-faint); }
    .settings-rail-item[aria-current="page"], .settings-rail-item:hover { background:none; color:var(--tone-strong); }
  }
  @media (prefers-reduced-motion: reduce) { .settings-rail-bubble { animation:none; } }
</style>
